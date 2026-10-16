#!/usr/bin/env python3
"""
lobx_cli.py — Interactive Visual Terminal Command Line Interface for LOB-X.

Provides:
  * ASCII calligraphy banner on launch
  * Dynamic command line prompt loop
  * Real-time dashboard showing the data collection process
  * ASCII terminal order book visualization (depth, bid/ask spread)
  * Candle / Price sparkline chart in the terminal
  * Trade feed list
  * Database & File collection stats
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Path configuration to find local lobx package
_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.market_data.collector import Collector
from lobx.market_data.trade_stream import TradeTick
from lobx.analytics.volatility import EWMAVolatilityEstimator
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.base import MarketState
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.live.quote_loop import StrategyRunner
from lobx.terminal.telemetry import TelemetryCollector
from lobx.backtest.replay import BacktestConfig, HistoricalReplay

# Suppress logging to make terminal UI clean
logging.getLogger("lobx").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("websockets").setLevel(logging.ERROR)

try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

CONSOLE = Console()

# ── ASCII Logo ────────────────────────────────────────────────────────────────
LOGO = """
[bold green]y
██╗      ██████╗ ██████╗       ██╗  ██╗
██║     ██╔═══██╗██╔══██╗      ╚██╗██╔╝
██║     ██║   ██║██████╔╝       ╚███╔╝
██║     ██║   ██║██╔══██╗       ██╔██╗
███████╗╚██████╔╝██████╔╝      ██╔╝ ██╗
╚══════╝ ╚═════╝ ╚═════╝       ╚═╝  ╚═╝
[/bold green]
[bold white]Limit Order Book Exchange[/bold white]
[dim]High-Frequency Trading Matching Engine & Simulator CLI[/dim]
"""
# ── Adaptive Terminal Price & Candle Chart ──────────────────────────────────────
class AdaptiveTerminalChart:
    """
    Auto-scaling terminal price chart supporting both Candlestick and Continuous Line
    modes, with configurable timeframes (1s, 5s, 15s, 1m).
    """

    def __init__(self, height: int = 10, bucket_s: int = 5, mode: str = "candle") -> None:
        self.height = height
        self.bucket_s = bucket_s
        self.mode = mode  # "candle" or "line"
        # Each candle: [open, high, low, close, bucket_id]
        self._candles: list[list[float]] = []
        self._current_bucket: int = -1

    def add_trade(self, price: float, qty: float, is_buy: bool) -> None:
        """Accumulate a trade tick into the active bucket."""
        bucket = int(time.time()) // self.bucket_s

        if not self._candles or bucket != self._current_bucket:
            self._candles.append([price, price, price, price, float(bucket)])
            self._current_bucket = bucket
            if len(self._candles) > 200:
                self._candles.pop(0)
        else:
            c = self._candles[-1]
            c[1] = max(c[1], price)  # High
            c[2] = min(c[2], price)  # Low
            c[3] = price             # Close

    def render(self, max_cols: int = 40) -> Text:
        """Render the chart in either Line or Candlestick mode with calibrated Y-axis."""
        if not self._candles:
            t = Text(justify="center")
            t.append("\n  Waiting for live trades…", style="dim cyan")
            return t

        if self.mode == "line":
            return self._render_line(max_cols)
        return self._render_candle(max_cols)

    def _render_line(self, max_cols: int) -> Text:
        """Render a smooth continuous line graph with calibrated Y-axis."""
        Y_W = 10
        n_pts = max(4, max_cols - Y_W - 1)
        visible = self._candles[-n_pts:]
        closes = [c[3] for c in visible]
        N = len(closes)
        H = self.height

        mn, mx = min(closes), max(closes)
        span = mx - mn or 1.0

        # Step calculation for clean Y-axis numbers
        step = 1.0
        for s in (0.1, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500):
            if span / s <= H:
                step = s
                break

        pad = span * 0.10
        p_hi = mx + pad
        p_lo = mn - pad
        p_span = p_hi - p_lo

        import math as _math
        base = _math.floor(p_lo / step) * step
        label_prices = []
        p = base
        while p <= p_hi + step:
            if p_lo <= p <= p_hi:
                label_prices.append(p)
            p += step
        label_prices = sorted(set([p_hi, p_lo] + label_prices))

        def _row(price: float) -> int:
            frac = (p_hi - price) / p_span
            return max(0, min(H - 1, round(frac * (H - 1))))

        label_rows: dict[int, float] = {}
        for lp in label_prices:
            label_rows[_row(lp)] = lp

        total_sublevels = H * 4
        blocks = [" ", " ", "▄", "▀", "█"]
        grid = [[" " for _ in range(N)] for _ in range(H)]
        color_grid = [["dim cyan" for _ in range(N)] for _ in range(H)]

        for c, val in enumerate(closes):
            frac = (val - p_lo) / p_span
            sub = int(frac * (total_sublevels - 1))
            r_idx = H - 1 - (sub // 4)
            r_idx = max(0, min(H - 1, r_idx))
            ch = blocks[(sub % 4) + 1]
            grid[r_idx][c] = ch

            if c > 0:
                is_up = val >= closes[c - 1]
                color_grid[r_idx][c] = "bold green" if is_up else "bold red"

        out = Text()
        for r in range(H):
            if r in label_rows:
                out.append(f"{label_rows[r]:>8,.2f} ", style="dim white")
                out.append("▏", style="dim")
            else:
                out.append(" " * Y_W + "▏", style="dim")

            for c in range(N):
                out.append(grid[r][c], style=color_grid[r][c])
            out.append("\n")

        out.append(" " * Y_W + "└" + "─" * N + "\n", style="dim")
        latest = closes[-1]
        out.append(f"  Line Chart ({self.bucket_s}s tf) │ Latest: ${latest:,.2f}", style="dim cyan")
        return out

    def _render_candle(self, max_cols: int) -> Text:
        """Render a Bloomberg-style candlestick chart."""
        Y_W = 10
        n_candles = max(4, (max_cols - Y_W - 1) // 2)
        visible = self._candles[-n_candles:]
        N = len(visible)
        H = self.height

        highs  = [c[1] for c in visible]
        lows   = [c[2] for c in visible]
        raw_hi = max(highs)
        raw_lo = min(lows)
        span   = raw_hi - raw_lo or 1.0

        for step in (0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500):
            if span / step <= H:
                break

        pad   = span * 0.12
        p_hi  = raw_hi + pad
        p_lo  = raw_lo - pad
        p_span = p_hi - p_lo

        import math as _math
        base = _math.floor(p_lo / step) * step
        label_prices = []
        p = base
        while p <= p_hi + step:
            if p_lo <= p <= p_hi:
                label_prices.append(p)
            p += step
        label_prices = sorted(set([p_hi, p_lo] + label_prices))

        def _row(price: float) -> int:
            frac = (p_hi - price) / p_span
            return max(0, min(H - 1, round(frac * (H - 1))))

        label_rows: dict[int, float] = {}
        for lp in label_prices:
            label_rows[_row(lp)] = lp

        EMPTY = (" ", "")
        grid: list[list[tuple[str, str]]] = [[EMPTY] * N for _ in range(H)]

        for col, (o, h, l, c, _) in enumerate(visible):
            is_up    = c >= o
            body_sty = "bold green" if is_up else "bold red"
            wick_sty = "green"      if is_up else "red"

            r_hi  = _row(h)
            r_lo  = _row(l)
            r_top = _row(max(o, c))
            r_bot = _row(min(o, c))

            for r in range(H):
                if r < r_hi or r > r_lo:
                    continue
                if r_top <= r <= r_bot:
                    grid[r][col] = ("█", body_sty)
                else:
                    grid[r][col] = ("│", wick_sty)

        out = Text()
        for r in range(H):
            if r in label_rows:
                out.append(f"{label_rows[r]:>8,.2f} ", style="dim white")
                out.append("▏", style="dim")
            else:
                out.append(" " * Y_W + "▏", style="dim")

            for col in range(N):
                ch, sty = grid[r][col]
                out.append(ch, style=sty)
                out.append(" ", style="")

            out.append("\n")

        out.append(" " * Y_W + "└" + "─" * (N * 2) + "\n", style="dim")
        o5, h5, l5, c5 = visible[-1][0], visible[-1][1], visible[-1][2], visible[-1][3]
        out.append(
            f"  O:{o5:,.2f} H:{h5:,.2f} L:{l5:,.2f} C:{c5:,.2f} ({self.bucket_s}s candle)",
            style="dim cyan"
        )
        return out




# ── Live Visual Terminal ──────────────────────────────────────────────────────
class LiveDashboard:
    """
    Full-screen real-time terminal dashboard running 100% in-memory without
    database or storage overhead.
    """

    def __init__(self, symbol: str, bucket_s: int = 5, mode: str = "candle") -> None:
        self.symbol = symbol.upper()
        self.bucket_s = bucket_s
        self.mode = mode
        self.start_time = time.time()
        self.trades_processed = 0
        self.total_volume = 0.0
        self.depth_events = 0
        self.taker_buy_vol = 0.0

        self.recent_trades: list[TradeTick] = []
        self.chart = AdaptiveTerminalChart(height=9, bucket_s=bucket_s, mode=mode)

        # Pure in-memory streaming: enable_storage=False eliminates all DB/Parquet overhead
        self.collector = Collector(
            symbol=self.symbol,
            enable_engine_mirror=False,
            enable_storage=False,
            heartbeat_s=10,
        )

        self.collector.on_book_update(self._on_book_update)
        self.collector.on_trade(self._on_trade)

    def _on_book_update(self, event: dict) -> None:
        self.depth_events += 1

    def _on_trade(self, tick: TradeTick) -> None:
        self.trades_processed += 1
        self.total_volume += tick.qty
        if not tick.is_buyer_maker:
            self.taker_buy_vol += tick.qty
        self.recent_trades.append(tick)
        if len(self.recent_trades) > 10:
            self.recent_trades.pop(0)

        self.chart.add_trade(tick.price, tick.qty, not tick.is_buyer_maker)

    def generate_layout(self) -> Layout:
        term_width = CONSOLE.width or 80

        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )

        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1)
        )

        layout["right"].split(
            Layout(name="chart", ratio=1),
            Layout(name="trades", size=10)
        )

        # ── 1. Header ─────────────────────────────────────────────────────────
        uptime = int(time.time() - self.start_time)
        hrs, mins, secs = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        ws_stat = self.collector.ws_status
        is_synced = self.collector.depth_mgr.is_synced
        is_syncing = self.collector.depth_mgr._state.syncing

        if is_synced:
            ws_pill = Text("● Live", style="bold green")
        elif ws_stat == "Connected" and is_syncing:
            ws_pill = Text("◑ Syncing book…", style="bold yellow")
        elif ws_stat in ("Connecting...", "Initializing..."):
            ws_pill = Text("◌ Connecting…", style="bold cyan")
        else:
            ws_pill = Text("✗ Reconnecting…", style="bold red")

        header_text = Text()
        header_text.append("LOB-X Terminal  │  ", style="bold green")
        header_text.append(f"{self.symbol}  │  ", style="bold white")
        header_text.append(f"{hrs:02d}:{mins:02d}:{secs:02d}  │  ", style="dim")
        header_text.append("Feed: ", style="dim")
        header_text.append_text(ws_pill)

        layout["header"].update(Panel(Align.center(header_text), border_style="green"))

        # ── 2. Order Book (Left Panel) ────────────────────────────────────────
        book = self.collector.depth_mgr.book
        bb = book.best_bid()
        ba = book.best_ask()

        mid_price = None
        micro_price = None
        spread = None

        if bb is not None and ba is not None and is_synced:
            book_table = Table(title="Live Order Book L2", show_header=True, expand=True, box=None)
            book_table.add_column("Side",      justify="center", width=5)
            book_table.add_column("Price ($)", justify="right")
            book_table.add_column("Size",      justify="right", width=8)
            book_table.add_column("Cumul.BTC", justify="right", width=8)
            book_table.add_column("Liq. Bar",  justify="left",  width=8)

            asks_view = list(book.asks.items())[:8]    # best→worst (ascending)
            bids_view = list(book.bids.items())[-8:]   # worst→best (ascending), best = last

            # ── Cumulative depth: running sum from mid outward ─────────────────
            # Asks: cumul from best ask (index 0) going up
            ask_cum: list[float] = []
            running = 0.0
            for _, qty in asks_view:
                running += qty
                ask_cum.append(running)

            # Bids: cumul from best bid (last item) going down
            bid_cum: list[float] = []
            running = 0.0
            for _, qty in reversed(bids_view):
                running += qty
                bid_cum.append(running)
            bid_cum.reverse()  # now index 0 = worst bid (deepest), -1 = best bid

            max_cum = max((ask_cum[-1] if ask_cum else 1.0), (bid_cum[0] if bid_cum else 1.0))

            # Compute quantitative microprice
            bid_p, bid_q = bb
            ask_p, ask_q = ba
            spread    = ask_p - bid_p
            mid_price = (ask_p + bid_p) / 2.0
            total_q   = bid_q + ask_q
            micro_price = (bid_q * ask_p + ask_q * bid_p) / total_q if total_q > 0 else mid_price

            # ── Display asks (worst → best, so highest price at top of screen) ──
            for i, (price, qty) in enumerate(reversed(asks_view)):
                # When reversed, index i=0 → asks_view[-1] (worst ask, highest price)
                cum = ask_cum[len(asks_view) - 1 - i]
                bar_len = max(1, round(cum / max_cum * 7))
                bar = "▓" * bar_len
                book_table.add_row(
                    "[red]ASK[/red]",
                    f"{price:,.2f}",
                    f"{qty:.4f}",
                    f"[red]{cum:.3f}[/red]",
                    f"[red]{bar}[/red]",
                )

            # ── Spread row ────────────────────────────────────────────────────
            book_table.add_row(
                "──", f"Spread: {spread:.2f}", "──────", "────────", "────────",
                style="dim"
            )

            # ── Display bids (best → worst, so highest price at top of bid section) ──
            for i, (price, qty) in enumerate(reversed(bids_view)):
                # reversed(bids_view): index 0 = bids_view[-1] = best bid
                # bid_cum is indexed 0=worst, -1=best  → we need bid_cum[len-1-i]
                cum = bid_cum[len(bids_view) - 1 - i]
                bar_len = max(1, round(cum / max_cum * 7))
                bar = "▓" * bar_len
                book_table.add_row(
                    "[green]BID[/green]",
                    f"{price:,.2f}",
                    f"{qty:.4f}",
                    f"[green]{cum:.3f}[/green]",
                    f"[green]{bar}[/green]",
                )

            layout["left"].update(Panel(book_table, border_style="cyan", title="Order Book L2  │  Cumul.BTC = running depth from mid"))

        else:
            # Book not yet synced — show status. Trades + chart still render normally below.
            status_txt = Text(justify="center")
            if ws_stat in ("Initializing...", "Connecting..."):
                status_txt.append("◌  Connecting to Binance…\n", style="bold cyan")
                status_txt.append("Initiating WebSocket streams…", style="dim")
            elif ws_stat == "Connected":
                status_txt.append("◑  Fetching L2 snapshot…\n", style="bold yellow")
                status_txt.append(f"Buffering depth events…  trades: {self.trades_processed}", style="yellow")
            else:
                status_txt.append("✗  Reconnecting…\n", style="bold red")
                status_txt.append("Auto-reconnecting endpoints…", style="dim")

            layout["left"].update(Panel(Align.center(status_txt, vertical="middle"), border_style="cyan", title="Order Book Depth"))

        # ── 3. Chart Panel — always render (trades flow before book sync) ─────
        avail_cols = max(12, (term_width // 2) - 15)
        chart_title = "Price Candles (5s)" if self.trades_processed > 0 else "Price Candles (5s) — waiting…"
        layout["chart"].update(Panel(self.chart.render(max_cols=avail_cols), border_style="magenta", title=chart_title))

        # ── 4. Trades Panel — always render ───────────────────────────────────
        trades_table = Table(show_header=True, expand=True, box=None)
        trades_table.add_column("Time", justify="center", width=8)
        trades_table.add_column("Side", justify="center", width=5)
        trades_table.add_column("Price ($)", justify="right")
        trades_table.add_column("Size", justify="right")

        for t in reversed(self.recent_trades):
            time_str = datetime.fromtimestamp(t.event_time / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            side_color = "red" if t.is_buyer_maker else "green"
            side_str = "SELL" if t.is_buyer_maker else "BUY"
            trades_table.add_row(
                time_str,
                f"[{side_color}]{side_str}[/{side_color}]",
                f"{t.price:,.2f}",
                f"{t.qty:.4f}"
            )

        layout["trades"].update(Panel(trades_table, border_style="yellow", title="Live Trade Feed"))

        # ── 5. Footer Stats ───────────────────────────────────────────────────
        # Show live trade price even before book sync
        live_price = self.recent_trades[-1].price if self.recent_trades else None
        mid_str = f"${mid_price:,.2f}" if mid_price else (f"${live_price:,.2f} (live)" if live_price else "—")
        micro_str = f"${micro_price:,.2f}" if micro_price else "—"
        spread_str = f"${spread:.2f}" if spread else "—"
        buy_pct = (self.taker_buy_vol / self.total_volume * 100) if self.total_volume > 0 else 50.0

        footer_text = Text()
        footer_text.append(f"Price: {mid_str}  │  ", style="bold white")
        footer_text.append(f"Micro: {micro_str}  │  ", style="bold cyan")
        footer_text.append(f"Spread: {spread_str}  │  ", style="bold yellow")
        footer_text.append(f"Vol: {self.total_volume:.2f} BTC (Buy {buy_pct:.0f}%)  │  ", style="bold green")
        footer_text.append(f"Trades: {self.trades_processed:,}  │  ", style="dim")
        footer_text.append("Ctrl+C to exit", style="dim yellow")

        layout["footer"].update(Panel(Align.center(footer_text), border_style="blue"))
        return layout

    async def run(self) -> None:
        """Run the live dashboard loop."""
        self._run_error: str | None = None
        ws_task = asyncio.create_task(self.collector.run())
        await asyncio.sleep(0)

        # Use screen=False (standard scroll) instead of screen=True (alternate buffer).
        # screen=True uses \033[?1049h which silently fails or raises KeyboardInterrupt
        # on some Windows terminal configurations (pwsh in certain console modes).
        try:
            with Live(
                self.generate_layout(),
                console=CONSOLE,
                screen=False,
                auto_refresh=False,
                vertical_overflow="visible",
            ) as live:
                while True:
                    await asyncio.sleep(0.25)
                    if ws_task.done() and not ws_task.cancelled():
                        try:
                            ws_task.result()
                        except Exception as exc:
                            self._run_error = str(exc)
                            break
                    try:
                        live.update(self.generate_layout(), refresh=True)
                    except Exception as render_exc:
                        # Log render errors but keep the loop alive
                        self._run_error = f"render: {render_exc}"
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        except Exception as exc:
            self._run_error = str(exc)
        finally:
            self.collector.stop()
            ws_task.cancel()
            try:
                await ws_task
            except (asyncio.CancelledError, Exception):
                pass
            await self.collector.close_async()


def _fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000.0:,.1f}s"
    return f"{ms:,.0f}ms"


def _render_backtest_report(result, symbol: str, source: str) -> None:
    """Print the backtest summary as plain, unembellished tables.

    Every number is read directly from ``result.summary`` (produced by
    HistoricalReplay: PnL waterfall, markouts, adverse selection, quote
    quality). No value is invented and no outcome is narrated.
    """
    s = result.summary
    net = s.get("net_pnl", 0.0)
    net_col = "bold green" if net >= 0 else "bold red"
    window_s = (result.ended_at - result.started_at) / 1000.0

    CONSOLE.print(f"\n[bold magenta]{'═' * 79}[/bold magenta]")
    CONSOLE.print(f"[bold magenta]  BACKTEST REPORT — {result.strategy}  │  {symbol.upper()}[/bold magenta]")
    CONSOLE.print(f"[bold magenta]{'═' * 79}[/bold magenta]")
    CONSOLE.print(f"[dim]Source: {source}   Window: {window_s:,.0f}s   "
                  f"Depth events: {result.depth_events:,}   Trades: {result.trade_events:,}[/dim]\n")

    # 1. PnL attribution waterfall — where every dollar came from.
    wf = Table(title="1. PnL Attribution (waterfall)", show_header=True, header_style="bold cyan")
    wf.add_column("Component", style="bold white")
    wf.add_column("USD", justify="right")
    wf.add_column("Meaning", style="dim")
    wf.add_row("Execution edge", f"{s.get('execution_edge', 0.0):+.4f}",
               "Quote-vs-mid edge at fill time (not yet realized)")
    wf.add_row("Realized inventory PnL", f"{s.get('realized_inventory_pnl', 0.0):+.4f}",
               "Closed round-trips, average-cost basis")
    wf.add_row("Unrealized inventory PnL", f"{s.get('unrealized_inventory_pnl', 0.0):+.4f}",
               "Open position marked to final mid")
    wf.add_row("Fees paid", f"{-s.get('fees', 0.0):+.4f}",
               "Maker fees on fills + taker fee on forced flatten")
    wf.add_row("Gross PnL", f"{s.get('gross_pnl', 0.0):+.4f}", "Realized + unrealized, before fees")
    wf.add_row("[bold]Net PnL[/bold]", f"[{net_col}]{net:+.4f}[/{net_col}]", "After fees — the bottom line")
    CONSOLE.print(wf)
    CONSOLE.print("")

    # 2. Fills, spread capture, and adverse selection.
    adv = Table(title="2. Fills & Adverse Selection", show_header=True, header_style="bold cyan")
    adv.add_column("Metric", style="bold white")
    adv.add_column("Value", justify="right")
    adv.add_column("Meaning", style="dim")
    total_fills = s.get("fills", 0)
    adv.add_row("Fills (maker / total)", f"{s.get('maker_fill_count', 0):,} / {total_fills:,}",
                "Times a real trade crossed our resting quote")
    adv.add_row("Filled qty (maker)", f"{s.get('maker_filled_qty', 0.0):.4f} BTC", "Volume we captured as a maker")
    adv.add_row("Turnover", f"${s.get('turnover', 0.0):,.2f}", "Notional traded")
    mm = s.get("mean_markout", 0.0)
    mm_col = "green" if mm >= 0 else "red"
    adv.add_row("Mean markout", f"[{mm_col}]{mm:+.5f}[/{mm_col}]",
                "Avg mid move after a fill; negative = picked off")
    adv.add_row("Adverse markout rate", f"{s.get('adverse_fill_rate', 0.0) * 100:.1f}%",
                f"Of markout observations pooled across horizons: "
                f"{s.get('adverse_fills', 0):,} adverse / {s.get('good_fills', 0):,} good / {s.get('neutral_fills', 0):,} neutral")
    # Markout term structure across horizons.
    horizons = [100, 500, 1000, 5000, 10000, 30000]
    parts = []
    for h in horizons:
        v = s.get(f"mean_{h}ms_markout")
        if v is not None:
            parts.append(f"{_fmt_ms(h)}={v:+.4f}")
    if parts:
        adv.add_row("Markout term structure", "", "  ".join(parts))
    CONSOLE.print(adv)
    CONSOLE.print("")

    # 3. Quote quality — how we sat in the book.
    qq = Table(title="3. Quote Quality", show_header=True, header_style="bold cyan")
    qq.add_column("Metric", style="bold white")
    qq.add_column("Value", justify="right")
    qq.add_column("Meaning", style="dim")
    qq.add_row("Mean quoted spread", f"${s.get('spread', 0.0):.4f}", "Average ask−bid we posted")
    qq.add_row("Mean distance from mid", f"${s.get('distance_from_mid_mean', 0.0):.4f}", "How far our bid sat from mid")
    qq.add_row("Mean quote lifetime", _fmt_ms(s.get("quote_lifetime_ms_mean")), "How long a quote rested before fill/replace")
    qq.add_row("Time quoted", f"{s.get('percentage_time_quoted', 0.0) * 100:.1f}%", "Share of session with a live two-sided quote")
    qq.add_row("Fill rate", f"{s.get('fill_rate_per_quote_second', 0.0):.4f}/quote-sec", "Fills per second of quote exposure")
    qq.add_row("Replacements", f"{s.get('replacements', 0):,}", "Cancel-replace count from repricing")
    CONSOLE.print(qq)
    CONSOLE.print("")

    # 4. Inventory & risk.
    rk = Table(title="4. Inventory & Risk", show_header=True, header_style="bold cyan")
    rk.add_column("Metric", style="bold white")
    rk.add_column("Value", justify="right")
    rk.add_column("Meaning", style="dim")
    rk.add_row("Final position", f"{s.get('final_position', 0.0):+.6f} BTC", "Flattened at end when configured")
    rk.add_row("Max |position|", f"{s.get('max_abs_position', 0.0):.6f} BTC", "Largest inventory held")
    rk.add_row("Mean |position|", f"{s.get('mean_abs_position', 0.0):.6f} BTC", "Average absolute inventory")
    rk.add_row("Inventory half-life", _fmt_ms(s.get("inventory_half_life_ms")), "Time for inventory to mean-revert")
    rk.add_row("Max drawdown", f"${s.get('max_drawdown', 0.0):.4f}", "Largest peak-to-trough equity drop")
    halted = s.get("risk_halted", False)
    rk.add_row("Risk halt", f"[red]{s.get('risk_halt_reason')}[/red]" if halted else "[green]none[/green]",
               "Hard risk limit breach, if any")
    CONSOLE.print(rk)
    CONSOLE.print("")


def _render_live_summary(runner, symbol: str, elapsed_s: int) -> None:
    """Print an end-of-session summary read from the live engine state.

    All figures come from runner.summary() (portfolio + risk). Reproducible
    markout / adverse-selection term structure is produced by `backtest`,
    since a live session cannot be replayed.
    """
    s = runner.summary()
    net = s.get("net_pnl", s.get("total_pnl", 0.0))
    net_col = "bold green" if net >= 0 else "bold red"

    CONSOLE.print("\n[bold magenta]═══════════════════════════════════════════════════════════════════════════════[/bold magenta]")
    CONSOLE.print(f"[bold magenta]  LIVE SESSION SUMMARY — {s.get('strategy', 'strategy')}  │  {symbol.upper()}[/bold magenta]")
    CONSOLE.print("[bold magenta]═══════════════════════════════════════════════════════════════════════════════[/bold magenta]")
    CONSOLE.print(f"[dim]Window: {elapsed_s}s   Ticks: {s.get('ticks', 0):,}   "
                  f"Fills: {s.get('fills', 0):,}[/dim]\n")

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Metric", style="bold white")
    t.add_column("Value", justify="right")
    t.add_column("Meaning", style="dim")
    t.add_row("Execution edge", f"{s.get('execution_edge', 0.0):+.4f}", "Quote-vs-mid edge at fill time")
    t.add_row("Realized PnL", f"{s.get('realized_pnl', 0.0):+.4f}", "Closed round-trips, average-cost basis")
    t.add_row("Unrealized PnL", f"{s.get('unrealized_pnl', 0.0):+.4f}", "Open position marked to last mid")
    t.add_row("Fees paid", f"-{s.get('fees_paid', s.get('fees', 0.0)):.4f}", "Maker fees on fills")
    t.add_row("[bold]Net PnL[/bold]", f"[{net_col}]{net:+.4f}[/{net_col}]", "After fees — the bottom line")
    t.add_row("Final position", f"{s.get('position', 0.0):+.6f} BTC", "Net BTC held at session end")
    t.add_row("Max |inventory|", f"{s.get('max_inventory', 0.0):.6f} BTC", "Largest position during the run")
    t.add_row("Inventory half-life", _fmt_ms(s.get("inventory_half_life_ms")), "Time for inventory to mean-revert")
    halted = s.get("risk_halted", False)
    t.add_row("Risk halt", f"[red]{s.get('risk_halt_reason')}[/red]" if halted else "[green]none[/green]",
              "Hard risk limit breach, if any")
    CONSOLE.print(t)
    CONSOLE.print("[dim]For reproducible markout / adverse-selection analysis over a fixed "
                  "dataset, run `backtest`.[/dim]\n")


# ── Interactive Command Loop ──────────────────────────────────────────────────
class LobxCLI:
    def __init__(self) -> None:
        self.running = True

    def show_welcome(self) -> None:
        CONSOLE.print(LOGO)
        CONSOLE.print("Type [bold yellow]help[/bold yellow] or [bold yellow]?[/bold yellow] to list available commands.\n")

    def run_command(self, cmd_line: str) -> None:
        parts = cmd_line.strip().split()
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("exit", "quit", "q"):
            self.running = False
            CONSOLE.print("[bold red]Exiting LOB-X CLI. Goodbye![/bold red]")

        elif cmd in ("help", "?"):
            self.cmd_help()

        elif cmd in ("live", "collect", "watch"):
            symbol = "BTCUSDT"
            bucket_s = 5
            mode = "candle"
            for a in args:
                al = a.lower()
                if al in ("line", "candle"):
                    mode = al
                elif al.endswith("s") and al[:-1].isdigit():
                    bucket_s = int(al[:-1])
                elif al.endswith("m") and al[:-1].isdigit():
                    bucket_s = int(al[:-1]) * 60
                elif not al.isdigit():
                    symbol = al.upper()
            self.cmd_live(symbol, bucket_s, mode)

        elif cmd in ("book", "depth", "snapshot"):
            symbol = args[0] if args else "BTCUSDT"
            self.cmd_book(symbol)

        elif cmd in ("as", "mm", "quote"):
            symbol = args[0] if args else "BTCUSDT"
            inv = float(args[1]) if len(args) > 1 else 0.0
            self.cmd_as(symbol, inv)

        elif cmd in ("sim", "dashboard", "engine", "run"):
            symbol = "BTCUSDT"
            duration_s = 60
            for a in args:
                if a.isdigit():
                    duration_s = int(a)
                else:
                    symbol = a.upper()
            self.cmd_sim(symbol, duration_s)

        elif cmd in ("backtest", "replay", "bt"):
            date = None
            minutes = None
            for a in args:
                if a.count("-") == 2:
                    date = a
                elif a.replace(".", "", 1).isdigit():
                    minutes = float(a)
            self.cmd_backtest(date=date, minutes=minutes)

        elif cmd == "status":
            self.cmd_status()

        else:
            CONSOLE.print(f"[bold red]Unknown command:[/bold red] '{cmd}'. Type 'help' for commands.")

    def cmd_help(self) -> None:
        table = Table(title="Available LOB-X CLI Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="bold yellow")
        table.add_column("Description")

        table.add_row("live [sym] [tf] [mode]", "Open real-time terminal with live L2 book & chart (e.g. 'live BTCUSDT 1s line' or 'live 5s candle').")
        table.add_row("book [symbol]", "Fetch and display an instant snapshot table of top bid/ask levels.")
        table.add_row("as [symbol] [q]", "Calculate Avellaneda-Stoikov reservation price & optimal spread for inventory q.")
        table.add_row("sim [sym] [secs]", "Run the Avellaneda-Stoikov strategy live on Binance data through the execution core, with a live telemetry view.")
        table.add_row("backtest [date] [min]", "Replay a captured session (data/raw) through the strategy + engine and print the PnL / markout / adverse-selection report.")
        table.add_row("status", "Display system environment, matching engine bridge, and network status.")
        table.add_row("help / ?", "Show this command listing.")
        table.add_row("exit / quit / q", "Exit the CLI session.")

        CONSOLE.print(table)

    def cmd_live(self, symbol: str, bucket_s: int = 5, mode: str = "candle") -> None:
        CONSOLE.print(f"\n[bold green]Launching Live Market Terminal for {symbol.upper()} ({bucket_s}s timeframe, {mode} mode)…[/bold green]")
        dashboard = LiveDashboard(symbol, bucket_s=bucket_s, mode=mode)
        try:
            asyncio.run(dashboard.run())
        except KeyboardInterrupt:
            pass
        except BaseException as exc:
            CONSOLE.print(f"[bold red]Live terminal error:[/bold red] {type(exc).__name__}: {exc}")

        if getattr(dashboard, "_run_error", None):
            CONSOLE.print(f"[bold red]Dashboard error:[/bold red] {dashboard._run_error}")

        CONSOLE.print("\n[bold green]Returned to LOB-X CLI menu.[/bold green]\n")

    def cmd_book(self, symbol: str) -> None:
        """Fetch a single snapshot of the current order book and print nicely."""
        import urllib.request
        import json
        CONSOLE.print(f"\n[bold cyan]Fetching instant L2 snapshot for {symbol.upper()}…[/bold cyan]")
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit=10"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode())

            bids = [(float(p), float(q)) for p, q in data["bids"]]
            asks = [(float(p), float(q)) for p, q in data["asks"]]

            bb_p, bb_q = bids[0]
            ba_p, ba_q = asks[0]
            spread = ba_p - bb_p
            mid = (ba_p + bb_p) / 2.0
            micro = (bb_q * ba_p + ba_q * bb_p) / (bb_q + ba_q)

            table = Table(title=f"Order Book Snapshot: {symbol.upper()}", show_header=True, header_style="bold cyan")
            table.add_column("Type", justify="center")
            table.add_column("Price ($)", justify="right")
            table.add_column("Quantity", justify="right")
            table.add_column("Notional ($)", justify="right")

            for p, q in reversed(asks[:5]):
                table.add_row("[red]ASK[/red]", f"{p:,.2f}", f"{q:.4f}", f"${p*q:,.2f}")

            table.add_row("─" * 4, f"Spread: {spread:.2f}", f"Mid: {mid:,.2f}", f"Micro: {micro:,.2f}", style="bold yellow")

            for p, q in bids[:5]:
                table.add_row("[green]BID[/green]", f"{p:,.2f}", f"{q:.4f}", f"${p*q:,.2f}")

            CONSOLE.print(table)
            CONSOLE.print("")
        except Exception as exc:
            CONSOLE.print(f"[bold red]Failed to fetch book snapshot:[/bold red] {exc}\n")

    def cmd_as(self, symbol: str, inventory: float) -> None:
        """Compute real-time Avellaneda-Stoikov market maker reservation quotes."""
        import urllib.request
        import json
        CONSOLE.print(f"\n[bold cyan]Calculating Avellaneda-Stoikov Quotes for {symbol.upper()} (Inventory q={inventory})…[/bold cyan]")
        try:
            url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol.upper()}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode())

            bid = float(data["bidPrice"])
            ask = float(data["askPrice"])
            mid = (bid + ask) / 2.0
            market_spread = ask - bid

            # AS Parameters: gamma (risk aversion), sigma (volatility), T-t (horizon), kappa (order arrival intensity)
            gamma = 0.1
            sigma = mid * 0.0005   # ~0.05% short-term volatility
            t_rem = 1.0            # 1 normalized trading session
            kappa = 1.5

            strategy = AvellanedaStoikovStrategy(
                gamma=gamma, kappa=kappa, horizon_seconds=1.0, qty=0.001
            )
            state = MarketState.from_book(
                timestamp=0,
                best_bid=bid,
                best_bid_qty=1.0,
                best_ask=ask,
                best_ask_qty=1.0,
                volatility=sigma,
                inventory=inventory,
            )
            quotes = strategy.on_tick(state)
            audit = strategy.last_quote_audit
            res_price = audit["reservation_price"]
            as_spread = audit["model_full_spread"]
            optimal_bid = quotes.bid.price if quotes.bid else mid
            optimal_ask = quotes.ask.price if quotes.ask else mid

            table = Table(title=f"Avellaneda-Stoikov Model ({symbol.upper()})", show_header=True, header_style="bold magenta")
            table.add_column("Metric", style="bold white")
            table.add_column("Value", justify="right")

            table.add_row("Market Mid Price", f"${mid:,.2f}")
            table.add_row("Market Spread", f"${market_spread:.2f}")
            table.add_row("Current Inventory (q)", f"{inventory:+.2f}")
            table.add_row("Reservation Price r(s,q)", f"${res_price:,.2f}", style="bold cyan")
            table.add_row("Optimal AS Spread", f"${as_spread:.2f}")
            table.add_row("Optimal Quote BID", f"${optimal_bid:,.2f}", style="bold green")
            table.add_row("Optimal Quote ASK", f"${optimal_ask:,.2f}", style="bold red")

            CONSOLE.print(table)
            CONSOLE.print("")
        except Exception as exc:
            CONSOLE.print(f"[bold red]Failed to compute AS model:[/bold red] {exc}\n")

    def cmd_backtest(self, date: str | None = None, minutes: float | None = None,
                     gamma: float = 0.1, kappa: float = 1.5) -> None:
        """Replay a captured session through the canonical AS strategy + engine.

        Uses HistoricalReplay over real captured depth+trade Parquet in data/raw.
        Prints the PnL waterfall, markouts, adverse selection, and quote quality.
        """
        data_dir = Path(__file__).resolve().parents[1] / "data" / "raw"
        if not data_dir.is_dir():
            CONSOLE.print(f"[bold red]No capture directory:[/bold red] {data_dir}\n")
            return

        # Pick the capture: an explicit date, else the largest depth file that
        # has a matching non-trivial trades file.
        def _pair(depth_path: Path) -> tuple[Path, Path] | None:
            trades_path = depth_path.with_name(depth_path.name.replace("depth_", "trades_", 1))
            if trades_path.exists() and trades_path.stat().st_size > 1024:
                return depth_path, trades_path
            return None

        pair: tuple[Path, Path] | None = None
        if date:
            candidate = data_dir / f"depth_{date}.parquet"
            pair = _pair(candidate) if candidate.exists() else None
            if pair is None:
                CONSOLE.print(f"[bold red]No usable capture for date {date} in {data_dir}[/bold red]\n")
                return
        else:
            candidates = sorted(data_dir.glob("depth_*.parquet"), key=lambda p: p.stat().st_size, reverse=True)
            for cand in candidates:
                pair = _pair(cand)
                if pair is not None:
                    break
            if pair is None:
                CONSOLE.print(f"[bold red]No usable depth+trades capture found in {data_dir}[/bold red]\n")
                return

        depth_path, trades_path = pair
        duration_ms = int(minutes * 60_000) if minutes else None
        horizon = (duration_ms / 1000.0) if duration_ms else 3600.0

        CONSOLE.print(f"\n[bold magenta]▶  Backtest on {depth_path.name}[/bold magenta]")
        CONSOLE.print(f"[dim]γ={gamma}  κ={kappa}  "
                      f"window={'full capture' if duration_ms is None else f'{minutes:g} min'}  "
                      f"maker_fee=2bps  queue_model=queue_ahead[/dim]")
        CONSOLE.print("[dim]Loading Parquet and replaying… (large captures take a moment)[/dim]")

        cfg = BacktestConfig(
            name="as_cli",
            quote_interval_ms=100,
            duration_ms=duration_ms,
            execution=ExecutionAssumptions(maker_fee_rate=0.0002, queue_model="queue_ahead"),
        )

        def _make_strategy():
            return AvellanedaStoikovStrategy(gamma=gamma, kappa=kappa,
                                             horizon_seconds=horizon, qty=0.001)

        try:
            replay = HistoricalReplay.from_parquet(depth_path, trades_path, cfg)
            result = replay.run(_make_strategy)
        except Exception as exc:
            CONSOLE.print(f"[bold red]Backtest failed:[/bold red] {type(exc).__name__}: {exc}\n")
            return

        _render_backtest_report(result, "BTCUSDT", source=depth_path.name)

    def cmd_sim(self, symbol: str = "BTCUSDT", duration_s: int = 60) -> None:
        """
        Live Avellaneda-Stoikov market-making simulation on REAL Binance data
        with real-time price & PnL charts and step-by-step mathematical breakdown.

        Ctrl+C to stop early.
        """
        CONSOLE.print(f"\n[bold magenta]▶  Live AS Market-Making Simulation on {symbol.upper()} ({duration_s}s)[/bold magenta]")
        CONSOLE.print("[dim]Connecting to Binance WebSocket… (Ctrl+C to stop)[/dim]\n")

        async def _run_sim():
            collector = Collector(
                symbol=symbol,
                enable_engine_mirror=False,
                enable_storage=False,
                heartbeat_s=60,
            )

            # ── Canonical execution core ──────────────────────────────────────
            gamma = 0.1
            kappa = 1.5
            strategy = AvellanedaStoikovStrategy(
                gamma=gamma, kappa=kappa, horizon_seconds=max(duration_s, 1), qty=0.001
            )
            runner = StrategyRunner(
                collector,
                strategy,
                execution_assumptions=ExecutionAssumptions(
                    maker_fee_rate=0.0002, queue_model="queue_ahead"
                ),
            )
            engine = runner.shadow_engine
            price_history: list[float] = []  # last N mid prices for chart
            pnl_history: list[float] = []    # last N net-pnl values for chart
            start = time.time()

            ws_task = asyncio.create_task(collector.run())
            await asyncio.sleep(0)

            def _render_chart_panel(data: list[float], height: int = 6, width: int = 32, prefix: str = "$", color_up: str = "bold green", color_dn: str = "bold red") -> Text:
                """Render a clean multi-line continuous trend graph with Y-axis labels."""
                if not data:
                    t = Text(justify="center")
                    t.append("\n  Accumulating chart data…", style="dim cyan")
                    return t

                n_pts = max(4, width - 11)
                sub = data[-n_pts:]
                N = len(sub)
                H = height

                mn, mx = min(sub), max(sub)
                span = mx - mn or 1.0
                pad = span * 0.10
                p_hi, p_lo = mx + pad, mn - pad
                p_span = p_hi - p_lo

                total_sublevels = H * 4
                blocks = [" ", " ", "▄", "▀", "█"]
                grid = [[" " for _ in range(N)] for _ in range(H)]
                styles = [["dim cyan" for _ in range(N)] for _ in range(H)]

                for c, val in enumerate(sub):
                    frac = (val - p_lo) / p_span
                    sub_idx = int(frac * (total_sublevels - 1))
                    r_idx = H - 1 - (sub_idx // 4)
                    r_idx = max(0, min(H - 1, r_idx))
                    grid[r_idx][c] = blocks[(sub_idx % 4) + 1]

                    if c > 0:
                        sty = color_up if val >= sub[c - 1] else color_dn
                        styles[r_idx][c] = sty
                    else:
                        styles[r_idx][c] = color_up if val >= 0 else color_dn

                out = Text()
                for r in range(H):
                    p_val = p_hi - (r / max(1, H - 1)) * p_span
                    if r == 0 or r == H - 1 or r == H // 2:
                        out.append(f"{prefix}{p_val:>8,.2f} ", style="dim white")
                    else:
                        out.append(" " * 10, style="")
                    out.append("▏", style="dim")
                    for c in range(N):
                        out.append(grid[r][c], style=styles[r][c])
                    out.append("\n")

                out.append(" " * 10 + "└" + "─" * N, style="dim")
                return out

            def _make_layout() -> Layout:
                layout = Layout()
                layout.split(
                    Layout(name="top",    size=12),
                    Layout(name="middle", ratio=1),
                    Layout(name="bottom", size=10),
                )
                layout["top"].split_row(
                    Layout(name="model",  ratio=1),
                    Layout(name="math",   ratio=1),
                )
                layout["middle"].split_row(
                    Layout(name="price_chart", ratio=1),
                    Layout(name="pnl_chart",   ratio=1),
                )
                layout["bottom"].split_row(
                    Layout(name="fills",  ratio=1),
                    Layout(name="footer", ratio=1),
                )

                elapsed = time.time() - start
                t_rem   = max(0.0, 1.0 - elapsed / duration_s)

                book   = collector.depth_mgr.book
                bb     = book.best_bid()
                ba     = book.best_ask()
                synced = collector.depth_mgr.is_synced
                mid    = runner._last_mid
                audit  = getattr(strategy, "last_quote_audit", {}) or {}

                # ── 1. Engine telemetry (read from the canonical core) ───────
                model_table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
                model_table.add_column("Metric", style="dim")
                model_table.add_column("Value",  justify="right")
                if mid and synced:
                    res        = audit.get("reservation_price", mid)
                    spread     = audit.get("model_full_spread", 0.0)
                    rb         = engine.resting_bid
                    ra         = engine.resting_ask
                    position   = engine.inventory.position
                    realized   = engine.inventory.realized_pnl
                    unrealized = engine.inventory.unrealized_pnl(mid)
                    fees       = engine.fees_paid
                    net        = engine.net_pnl(mid)
                    price_history.append(mid)
                    pnl_history.append(net)
                    if len(price_history) > 120: price_history.pop(0)
                    if len(pnl_history) > 120: pnl_history.pop(0)
                    inv_col = "green" if position >= 0 else "red"
                    net_col = "bold green" if net >= 0 else "bold red"
                    model_table.add_row("Status", "[bold green]● LIVE — canonical core[/bold green]")
                    model_table.add_row("Market Mid (s)", f"[bold white]${mid:,.2f}[/bold white]")
                    model_table.add_row("Reservation (r)", f"[bold cyan]${res:,.2f}[/bold cyan]")
                    model_table.add_row("Model Spread (δ)", f"[yellow]${spread:.4f}[/yellow]")
                    model_table.add_row("Our BID", "[bold green]—[/bold green]" if rb is None else f"[bold green]${rb.price:,.2f}[/bold green]")
                    model_table.add_row("Our ASK", "[bold red]—[/bold red]" if ra is None else f"[bold red]${ra.price:,.2f}[/bold red]")
                    model_table.add_row("Inventory (q)", f"[{inv_col}]{position:+.6f} BTC[/{inv_col}]")
                    model_table.add_row("Fills", f"{engine.fill_count:,}")
                    model_table.add_row("Realized PnL", f"${realized:+.4f}")
                    model_table.add_row("Unrealized PnL", f"${unrealized:+.4f}")
                    model_table.add_row("Fees Paid", f"[red]-${fees:.4f}[/red]")
                    model_table.add_row("Net PnL (mark)", f"[{net_col}]${net:+.4f}[/{net_col}]")
                else:
                    model_table.add_row("Status", f"[yellow]◑ {collector.ws_status}[/yellow]")
                layout["model"].update(Panel(model_table, border_style="magenta", title="Live AS Engine Telemetry"))

                # ── 2. Formula & inventory-skew transparency ─────────────────
                math_txt = Text()
                if mid and synced:
                    rb       = engine.resting_bid
                    ra       = engine.resting_ask
                    position = engine.inventory.position
                    sigma    = audit.get("sigma")
                    skew     = audit.get("inventory_skew")

                    math_txt.append("Quotes vs best bid/ask:\n", style="bold white")
                    if bb and ba:
                        math_txt.append(f"  bb ${bb[0]:,.2f}", style="dim green")
                        math_txt.append("   bid " + ("—" if rb is None else f"${rb.price:,.2f}"), style="bold green")
                        math_txt.append("  |  ", style="dim white")
                        math_txt.append("ask " + ("—" if ra is None else f"${ra.price:,.2f}"), style="bold red")
                        math_txt.append(f"   ba ${ba[0]:,.2f}\n\n", style="dim red")

                    math_txt.append("Inventory gauge:\n", style="bold white")
                    bar_w = 20
                    inv_norm = max(-1.0, min(1.0, position / 0.005))
                    pos = max(0, min(bar_w - 1, int((inv_norm + 1.0) / 2.0 * (bar_w - 1))))
                    gauge = ["░"] * bar_w
                    gauge[pos] = "▓"
                    gcol = "green" if position >= 0 else "red"
                    math_txt.append(f"  SHORT [{''.join(gauge)}] LONG  ({position:+.6f} BTC)\n\n", style=f"bold {gcol}")

                    math_txt.append("AS model:\n", style="bold white")
                    math_txt.append("  r = s − q·γ·σ²(T−t)\n", style="cyan")
                    math_txt.append("  δ = γσ²(T−t) + (2/γ)ln(1+γ/κ)\n", style="yellow")
                    if sigma is not None:
                        math_txt.append(f"  σ = {sigma:.3e}   ", style="dim")
                    if skew is not None:
                        math_txt.append(f"inventory skew (r−s) = {skew:+.3e} USD\n", style="dim")
                else:
                    math_txt.append("\n  Waiting for book sync…", style="dim")

                layout["math"].update(Panel(math_txt, border_style="cyan", title="AS Model — formula & inventory skew"))

                # ── 3. Charts Panels (Mid Price & PnL Curves) ───────────────
                term_w = CONSOLE.width or 80
                chart_w = max(15, (term_w // 2) - 14)

                p_chart = _render_chart_panel(price_history, height=6, width=chart_w, prefix="$")
                layout["price_chart"].update(Panel(p_chart, border_style="magenta", title=f"Mid Price Trend (${price_history[-1]:,.2f})" if price_history else "Mid Price Trend"))

                pnl_val = pnl_history[-1] if pnl_history else 0.0
                pnl_col = "bold green" if pnl_val >= 0 else "bold red"
                pnl_chart = _render_chart_panel(pnl_history, height=6, width=chart_w, prefix="$", color_up="bold green", color_dn="bold red")
                
                # Title with parsed formatted text
                pnl_title = Text()
                pnl_title.append("Cumulative PnL Curve (")
                pnl_title.append(f"${pnl_val:+.4f}", style=pnl_col)
                pnl_title.append(")")
                layout["pnl_chart"].update(Panel(pnl_chart, border_style="green" if pnl_val >= 0 else "red", title=pnl_title))

                # ── 4. Recent fills (from the engine ledger) ─────────────────
                fill_txt = Text()
                recent = engine.fills[-6:]
                if recent:
                    for f in reversed(recent):
                        side = f.get("side", "")
                        scol = "bold green" if side == "buy" else "bold red"
                        fill_txt.append(f"  {side.upper():4s} {f.get('qty', 0.0):.6f} @ ${f.get('price', 0.0):,.2f}\n", style=scol)
                else:
                    fill_txt.append("\n  No fills yet…\n", style="dim")
                n_buy  = sum(1 for f in engine.fills if f.get("side") == "buy")
                n_sell = engine.fill_count - n_buy
                layout["fills"].update(Panel(fill_txt, border_style="yellow", title=f"Fills ({n_buy} buy / {n_sell} sell)"))

                # ── 5. Footer ────────────────────────────────────────────────
                halt = f"  [bold red]RISK HALT: {runner.risk_manager.halt_reason}[/bold red]" if runner.risk_manager.halted else ""
                desc = Text.from_markup(
                    "[bold white]Avellaneda-Stoikov[/bold white] via the canonical core "
                    "(strategy → risk → shadow fill engine → portfolio).  "
                    f"[dim cyan]Progress: {int(elapsed)}s / {duration_s}s.[/dim cyan]  "
                    f"[dim]Ctrl+C to stop.[/dim]{halt}"
                )
                layout["footer"].update(Panel(desc, border_style="blue",
                    title=f"LOB-X  │  {symbol.upper()}  │  γ={gamma}  κ={kappa}  maker_fee=2bps"))

                return layout

            try:
                with Live(
                    _make_layout(), console=CONSOLE, screen=False,
                    auto_refresh=False, vertical_overflow="crop",
                ) as live:
                    deadline = start + duration_s
                    while time.time() < deadline:
                        await asyncio.sleep(0.5)
                        live.update(_make_layout(), refresh=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                collector.stop()
                ws_task.cancel()
                try: await ws_task
                except: pass
                await collector.close_async()

            _render_live_summary(runner, symbol, int(time.time() - start))

        try:
            asyncio.run(_run_sim())
        except KeyboardInterrupt:
            pass
        except BaseException as exc:
            CONSOLE.print(f"[bold red]Simulation error:[/bold red] {type(exc).__name__}: {exc}\n")


    def cmd_status(self) -> None:
        CONSOLE.print("\n[bold cyan]LOB-X System Status[/bold cyan]")
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Component", style="bold white")
        table.add_column("Status")

        try:
            import lobx_cpp
            cpp_status = "[bold green]Available (C++ pybind11 matching engine)[/bold green]"
        except ImportError:
            cpp_status = "[yellow]Not Built (Pure Python mode active)[/yellow]"

        table.add_row("Matching Engine (C++)", cpp_status)
        table.add_row("Market Data Mode", "[bold green]Pure In-Memory Live Streaming[/bold green]")
        table.add_row("Primary Feed", "Binance Spot WebSocket (data-stream.binance.vision / stream.binance.com)")
        table.add_row("Terminal UI", "[bold green]Rich Adaptive TUI[/bold green]")

        CONSOLE.print(table)
        CONSOLE.print("")

    def loop(self) -> None:
        self.show_welcome()
        while self.running:
            try:
                cmd_line = Prompt.ask("[bold cyan]lobx-cli[/bold cyan]")
                self.run_command(cmd_line)
            except (KeyboardInterrupt, EOFError):
                CONSOLE.print("\n")
                self.running = False
                CONSOLE.print("[bold red]Exiting LOB-X CLI. Goodbye![/bold red]")


if __name__ == "__main__":
    if not HAS_RICH:
        print("ERROR: Missing dependency 'rich'. Install it with: pip install rich")
        sys.exit(1)

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    cli = LobxCLI()
    cli.loop()

