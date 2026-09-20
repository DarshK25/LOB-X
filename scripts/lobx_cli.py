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

        elif cmd in ("sim", "backtest"):
            self.cmd_sim()

        elif cmd in ("hft", "engine", "dashboard"):
            symbol = args[0] if args else "BTCUSDT"
            self.cmd_hft(symbol)

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
        table.add_row("sim", "Run live Avellaneda-Stoikov market-making simulation on real Binance WebSocket feeds.")
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

            # ── Simulation state ──────────────────────────────────────────────
            gamma   = 0.1     # risk aversion
            kappa   = 1.5     # order arrival intensity
            canonical_strategy = AvellanedaStoikovStrategy(
                gamma=gamma, kappa=kappa, horizon_seconds=max(duration_s, 1), qty=0.001
            )
            volatility_estimator = EWMAVolatilityEstimator(decay=0.98)
            inventory = 0.0   # BTC held (can go negative for short)
            cash      = 0.0   # realised cash from fills
            fills: list[str] = []
            n_buys = n_sells = 0
            sigma_window: list[float] = []   # rolling mid prices for vol
            price_history: list[float] = []  # last N mid prices for chart
            pnl_history: list[float] = []    # last N pnl values for chart
            start = time.time()

            def _as_quotes(mid: float, t_rem: float) -> tuple[float, float, float, float, float]:
                """Return canonical AS (reservation, spread, bid, ask, sigma)."""
                book = collector.depth_mgr.book
                bid = book.best_bid()
                ask = book.best_ask()
                if bid is None or ask is None:
                    return mid, 0.0, mid, mid, 0.0
                sigma = volatility_estimator.current()
                state = MarketState.from_book(
                    timestamp=int(time.time() * 1000),
                    best_bid=bid[0], best_bid_qty=bid[1],
                    best_ask=ask[0], best_ask_qty=ask[1],
                    volatility=sigma, inventory=inventory,
                )
                quotes = canonical_strategy.on_tick(state)
                audit = canonical_strategy.last_quote_audit
                return (
                    audit["reservation_price"],
                    audit["model_full_spread"],
                    quotes.bid.price if quotes.bid else mid,
                    quotes.ask.price if quotes.ask else mid,
                    sigma,
                )

            def _on_trade(tick: "TradeTick") -> None:
                nonlocal inventory, cash, n_buys, n_sells
                sigma_window.append(tick.price)
                if len(sigma_window) > 30:
                    sigma_window.pop(0)

                book = collector.depth_mgr.book
                bb   = book.best_bid()
                ba   = book.best_ask()
                if bb is None or ba is None or not collector.depth_mgr.is_synced:
                    return
                mid   = (bb[0] + ba[0]) / 2.0
                volatility_estimator.update(mid)
                t_rem = 1.0 - min(1.0, (time.time() - start) / (duration_s * 60))
                _, _, our_bid, our_ask, _ = _as_quotes(mid, t_rem)

                # Taker SELL hits our BID quote → we buy
                if tick.is_buyer_maker and tick.price <= our_bid:
                    qty = min(tick.qty, 0.01)   # cap fill at 0.01 BTC
                    inventory += qty
                    cash      -= qty * tick.price
                    n_buys    += 1
                    fills.append(f"BUY  {qty:.4f} @ {tick.price:,.2f}")
                    if len(fills) > 6: fills.pop(0)

                # Taker BUY lifts our ASK quote → we sell
                elif not tick.is_buyer_maker and tick.price >= our_ask:
                    qty = min(tick.qty, 0.01)
                    inventory -= qty
                    cash      += qty * tick.price
                    n_sells   += 1
                    fills.append(f"SELL {qty:.4f} @ {tick.price:,.2f}")
                    if len(fills) > 6: fills.pop(0)

            collector.on_trade(_on_trade)
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

                book = collector.depth_mgr.book
                bb   = book.best_bid()
                ba   = book.best_ask()
                synced = collector.depth_mgr.is_synced

                # ── 1. Model panel ───────────────────────────────────────────
                model_table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
                model_table.add_column("Metric", style="dim")
                model_table.add_column("Value",  justify="right")

                if bb and ba and synced:
                    mid = (bb[0] + ba[0]) / 2.0
                    res, δ, our_bid, our_ask, sigma = _as_quotes(mid, t_rem)
                    unrealized = inventory * mid
                    total_pnl = cash + unrealized

                    price_history.append(mid)
                    pnl_history.append(total_pnl)
                    if len(price_history) > 120: price_history.pop(0)
                    if len(pnl_history) > 120: pnl_history.pop(0)

                    model_table.add_row("Status",        "[bold green]● LIVE STREAMING[/bold green]")
                    model_table.add_row("Market Mid (s)", f"[bold white]${mid:,.2f}[/bold white]")
                    model_table.add_row("Reservation (r)", f"[bold cyan]${res:,.2f}[/bold cyan]")
                    model_table.add_row("AS Spread (δ)", f"[yellow]${δ:.4f}[/yellow]")
                    model_table.add_row("Our BID Quote", f"[bold green]${our_bid:,.2f}[/bold green]")
                    model_table.add_row("Our ASK Quote", f"[bold red]${our_ask:,.2f}[/bold red]")
                    model_table.add_row("Inventory (q)", f"[{'green' if inventory >= 0 else 'red'}]{inventory:+.6f} BTC[/{'green' if inventory >= 0 else 'red'}]")
                    model_table.add_row("Realized Cash", f"${cash:+.2f}")
                    model_table.add_row("Unrealized Value", f"${unrealized:+.2f}")
                    model_table.add_row("Total Mkt-to-Mkt P&L", f"[{'bold green' if total_pnl >= 0 else 'bold red'}]${total_pnl:+.4f}[/{'bold green' if total_pnl >= 0 else 'bold red'}]")
                else:
                    model_table.add_row("Status", f"[yellow]◑ {collector.ws_status}[/yellow]")

                layout["model"].update(Panel(model_table, border_style="magenta", title="Live AS Engine Telemetry"))

                # ── 2. Math Formula & Risk Gauge Panel (Image 4 Replica) ─────
                math_txt = Text()
                if bb and ba and synced:
                    mid = (bb[0] + ba[0]) / 2.0
                    res, δ, our_bid, our_ask, sigma = _as_quotes(mid, t_rem)
                    skew = inventory * gamma * (sigma**2) * max(t_rem, 0.01)

                    # Active Quotes vs Best Bid/Ask Slider
                    math_txt.append("Active Quotes vs Best Bid/Ask:\n", style="bold white")
                    math_txt.append(f"  [bb ${bb[0]:,.2f} ─ ", style="dim green")
                    math_txt.append(f"bid ${our_bid:,.2f}", style="bold green")
                    math_txt.append(" ─ mid ─ ", style="dim white")
                    math_txt.append(f"ask ${our_ask:,.2f}", style="bold red")
                    math_txt.append(f" ─ ba ${ba[0]:,.2f}]\n\n", style="dim red")

                    # Visual Inventory Risk Gauge
                    math_txt.append("Inventory Risk Gauge:\n", style="bold white")
                    bar_w = 20
                    inv_norm = max(-1.0, min(1.0, inventory / 0.1))  # scale ±0.1 BTC
                    pos = int((inv_norm + 1.0) / 2.0 * (bar_w - 1))
                    gauge = ["░"] * bar_w
                    gauge[pos] = "▓"
                    gauge_str = "".join(gauge)
                    gauge_col = "green" if inventory >= 0 else "red"
                    math_txt.append(f"  SHORT [{gauge_str}] LONG  ({inventory:+.4f} BTC)\n\n", style=f"bold {gauge_col}")

                    # Math formula substitution
                    math_txt.append("Formula Substitution:\n", style="bold white")
                    math_txt.append(f"  r = s - q·γ·σ²(T-t) = ${res:,.2f} (Skew: ${skew:+.4f})\n", style="cyan")
                    math_txt.append(f"  δ = γσ²(T-t) + (2/γ)ln(1+γ/κ) = ${δ:.4f}\n", style="yellow")
                else:
                    math_txt.append("\n  Waiting for book sync…", style="dim")

                layout["math"].update(Panel(math_txt, border_style="cyan", title="AS Model Risk Gauge & Formula Breakdown"))

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

                # ── 4. Fills Panel ────────────────────────────────────────────
                fill_txt = Text()
                if fills:
                    for f in reversed(fills):
                        side_color = "bold green" if f.startswith("BUY") else "bold red"
                        fill_txt.append(f"  {f}\n", style=side_color)
                else:
                    fill_txt.append("\n  Waiting for fills…\n", style="dim")

                layout["fills"].update(Panel(fill_txt, border_style="yellow", title=f"Fills ({n_buys} Buys / {n_sells} Sells)"))

                # ── 5. Bottom Description Panel ──────────────────────────────
                desc = Text()
                desc.append("Avellaneda-Stoikov (2008) Market Maker. ", style="bold white")
                desc.append("Listens to real Binance order book & trade ticks. ", style="dim")
                desc.append("Virtual quotes automatically skew to manage inventory risk. ", style="dim yellow")
                desc.append(f"Progress: {int(elapsed)}s / {duration_s}s ({int((1-t_rem)*100)}%). ", style="dim cyan")
                desc.append("Ctrl+C to stop early.", style="dim")
                layout["footer"].update(Panel(desc, border_style="blue", title=f"LOB-X AS Engine  │  Symbol: {symbol.upper()}  │  γ={gamma}  κ={kappa}"))

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

            # ── Comprehensive Quantitative Performance Analysis ────────────────
            book = collector.depth_mgr.book
            bb, ba = book.best_bid(), book.best_ask()
            final_mid = (bb[0] + ba[0]) / 2.0 if bb and ba else 0.0
            unrealized_pnl = inventory * final_mid
            total_pnl = cash + unrealized_pnl
            tot_fills = n_buys + n_sells

            CONSOLE.print(f"\n[bold magenta]═══════════════════════════════════════════════════════════════════════════════[/bold magenta]")
            CONSOLE.print(f"[bold magenta]           AVELANEDA-STOIKOV SIMULATION PERFORMANCE ANALYSIS           [/bold magenta]")
            CONSOLE.print(f"[bold magenta]═══════════════════════════════════════════════════════════════════════════════[/bold magenta]\n")

            summary = Table(title=f"1. Core Execution Summary ({symbol.upper()})", show_header=True, header_style="bold cyan")
            summary.add_column("Metric",              style="bold white")
            summary.add_column("Value",               justify="right")
            summary.add_column("Financial Explanation", style="dim")

            summary.add_row("Simulation Runtime",    f"{int(time.time() - start)}s", "Total live execution window")
            summary.add_row("Total Virtual Fills",    f"{tot_fills:,}", "Executed when real trades crossed our quotes")
            summary.add_row("Buy Fills / Sell Fills",  f"[green]{n_buys}[/green] / [red]{n_sells}[/red]", f"Buy ratio: {(n_buys/tot_fills*100 if tot_fills else 0):.1f}%")
            summary.add_row("Final Inventory (q)",    f"[{'green' if inventory>=0 else 'red'}]{inventory:+.6f} BTC[/{'green' if inventory>=0 else 'red'}]", "Net BTC position held at session end")
            summary.add_row("Realized Cash Balance",  f"${cash:+.2f}", "Net cash accumulated from completed trades")
            summary.add_row("Unrealized Inventory PnL", f"${unrealized_pnl:+.2f}", f"Mark-to-market value of {inventory:+.4f} BTC")
            summary.add_row("Net Mark-to-Market PnL", f"[{'bold green' if total_pnl >= 0 else 'bold red'}]${total_pnl:+.4f}[/{'bold green' if total_pnl >= 0 else 'bold red'}]", "Total PnL = Realized Cash + Unrealized Inventory")

            CONSOLE.print(summary)
            CONSOLE.print("")

            # ── 2. Educational Breakdown Panel ──────────────────────────────
            edu_table = Table(title="2. Deconstructing The PnL & Model Behavior", show_header=True, header_style="bold yellow")
            edu_table.add_column("Behavior / Phenomenon", style="bold yellow")
            edu_table.add_column("What Happened & Why", style="white")

            skew_dir = "Downward (lowered quotes to sell)" if inventory > 0 else "Upward (raised quotes to buy)"
            pnl_nature = "Positive market drift on long inventory" if unrealized_pnl > 0 else "Negative inventory draw-down"

            edu_table.add_row(
                "Why PnL was +" + f"${total_pnl:.2f}",
                f"Your PnL has TWO components: (1) Spread capture on {tot_fills} micro-trades, and (2) {pnl_nature}. In HFT, market-makers earn tiny spreads, but taking inventory exposes them to price drift."
            )
            edu_table.add_row(
                "Inventory Skewing",
                f"Your final inventory was {inventory:+.4f} BTC. As $q$ shifted away from zero, AS automatically applied {skew_dir} skew to force inventory back toward 0."
            )
            edu_table.add_row(
                "Sophistication vs Real Hedge Funds",
                "LOB-X implements pure Avellaneda-Stoikov (2008). Institutional HFTs (Jump, Citadel, Tower) build on top of this by adding: (1) Microstructure signals (order flow toxicity), (2) Queue priority estimation, (3) Adverse selection filters, and (4) FPGA/Sub-microsecond execution."
            )

            CONSOLE.print(edu_table)
            CONSOLE.print("")

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

