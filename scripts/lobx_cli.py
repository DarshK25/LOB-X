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

import asyncio
import logging
import re
import sys
import time
from pathlib import Path

# Path configuration to find local lobx package
_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.backtest.replay import BacktestConfig, HistoricalReplay
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.live.quote_loop import StrategyRunner
from lobx.market_data.collector import Collector
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.base import MarketState
from lobx.terminal.dashboard import LiveDashboard

# Suppress logging to make terminal UI clean
logging.getLogger("lobx").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("websockets").setLevel(logging.ERROR)

try:
    from rich.console import Console
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
                elif al.endswith(("s", "m")) and any(ch.isdigit() for ch in al):
                    match = re.fullmatch(r"([0-9]+)([sm])", al)
                    if match is None:
                        CONSOLE.print("[bold red]Invalid timeframe. Use whole positive units, e.g. 1s or 1m.[/bold red]")
                        return
                    try:
                        bucket_s = int(match[1]) * (60 if match[2] == "m" else 1)
                    except ValueError:
                        CONSOLE.print("[bold red]Timeframe is too large.[/bold red]")
                        return
                elif not al.isdigit():
                    symbol = al.upper()
                else:
                    CONSOLE.print("[bold red]Timeframe needs a unit, e.g. 5s or 1m.[/bold red]")
                    return
            if bucket_s <= 0:
                CONSOLE.print("[bold red]Timeframe must be positive, e.g. 1s, 5s, 15s or 1m.[/bold red]")
                return
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

        table.add_row("live [sym] [tf] [mode]", "Live candles + volume, L2 and microstructure. Try 'live BTCUSDT 5s candle' or 'live 1s line'.")
        table.add_row("collect / watch", "Aliases for live (in-memory only; use run_collector.py to record data).")
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
        try:
            dashboard = LiveDashboard(symbol, bucket_s=bucket_s, mode=mode, console=CONSOLE)
        except ValueError as exc:
            CONSOLE.print(f"[bold red]Invalid live configuration:[/bold red] {exc}")
            return
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
            import lobx_cpp  # noqa: F401 - probe the compiled extension's actual loadability.
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

