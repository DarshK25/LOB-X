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
[bold green]
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
# ── Terminal Chart Helper ──────────────────────────────────────────────────────
# Unicode "sub-character" bars — each is 1 column wide but visually thinner.
# We use them for wick vs body so the chart doesn't look like a solid block grid.
_WICK_CHAR  = "│"   # thin vertical — wick above/below body
_BODY_UP    = "▓"   # filled bar — bullish body
_BODY_DOWN  = "░"   # lighter fill — bearish body
_EMPTY      = " "


class TerminalCandleChart:
    """
    Compact ASCII candlestick chart rendered inside a Rich Text panel.

    Design goals
    ────────────
    • Each candle column is exactly 1 character wide → no oversized blocks.
    • Candles bucket real time: 1 candle = BUCKET_S seconds.
    • Y-axis price labels printed on the right edge of every chart row.
    • The visible price range has a 20 % padding margin so tiny ticks don't
      fill the whole chart height (making every move look violent).
    • Colors: green (▓) for up-candles, red (░) for down-candles.
    """

    BUCKET_S: int = 10   # seconds per candle

    def __init__(self, width: int = 55, height: int = 10) -> None:
        self.width = width          # number of candles visible at once
        self.height = height        # terminal rows used for the chart grid
        # Each candle: (open, high, low, close, bucket_id)
        self._candles: list[tuple[float, float, float, float, int]] = []
        self._current_bucket: int = -1

    # ── Public API ────────────────────────────────────────────────────────────

    def add_trade(self, price: float, qty: float, is_buy: bool) -> None:
        """Accumulate a trade tick into the current time-bucket candle."""
        bucket = int(time.time()) // self.BUCKET_S

        if not self._candles or bucket != self._current_bucket:
            # Start a new candle for the new time bucket
            self._candles.append((price, price, price, price, bucket))
            self._current_bucket = bucket
            # Keep only the last `width` candles
            if len(self._candles) > self.width:
                self._candles.pop(0)
        else:
            # Update the current candle in-place
            o, h, l, c, b = self._candles[-1]
            self._candles[-1] = (o, max(h, price), min(l, price), price, b)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def render(self) -> Text:
        """Return a Rich Text object with the full chart ready to print."""
        if len(self._candles) < 2:
            t = Text()
            t.append("  Waiting for candle data", style="dim")
            if self._candles:
                t.append(f"  (1 tick @ {self._candles[0][3]:,.2f})", style="dim")
            return t

        candles = self._candles  # alias

        # ── Price range with padding ──────────────────────────────────────────
        all_h = [c[1] for c in candles]
        all_l = [c[2] for c in candles]
        raw_high = max(all_h)
        raw_low  = min(all_l)
        raw_span = raw_high - raw_low or 1.0

        # 20 % vertical padding so extreme moves don't fully saturate the chart
        pad   = raw_span * 0.20
        p_high = raw_high + pad
        p_low  = raw_low  - pad
        p_span = p_high - p_low

        # ── Build character grid ──────────────────────────────────────────────
        # grid[row][col] = (char, style_str)   row 0 = top
        num_cols = len(candles)
        grid: list[list[tuple[str, str]]] = [
            [(_EMPTY, "") for _ in range(num_cols)]
            for _ in range(self.height)
        ]

        def _price_to_row(p: float) -> int:
            """Convert price to grid row (0 = top, height-1 = bottom)."""
            frac = (p_high - p) / p_span          # 0 at top, 1 at bottom
            return max(0, min(self.height - 1, int(frac * (self.height - 1))))

        for col, (o, h, l, c, _) in enumerate(candles):
            is_up     = c >= o
            body_char = _BODY_UP if is_up else _BODY_DOWN
            color     = "green"  if is_up else "red"

            row_h = _price_to_row(h)
            row_l = _price_to_row(l)
            row_o = _price_to_row(max(o, c))   # open/close top
            row_c = _price_to_row(min(o, c))   # open/close bottom

            # Full wick span (thin │)
            for r in range(row_h, row_l + 1):
                grid[r][col] = (_WICK_CHAR, f"dim {color}")

            # Body span (solid block)
            for r in range(row_o, row_c + 1):
                grid[r][col] = (body_char, color)

        # ── Assemble Rich Text ────────────────────────────────────────────────
        out = Text()
        for r in range(self.height):
            # Right-edge Y-axis label: price at this row
            row_price = p_high - (r / (self.height - 1)) * p_span

            for col in range(num_cols):
                ch, st = grid[r][col]
                out.append(ch, style=st)

            # Price label on the right (6 chars wide, 2 decimal places)
            out.append(f" {row_price:>10,.2f}", style="dim")
            out.append("\n")

        # Time-scale caption
        if candles:
            latest_price = candles[-1][3]
            out.append(
                f" {self.BUCKET_S}s candles │ latest ${latest_price:,.2f}",
                style="dim"
            )
        return out




# ── Live Collector Dashboard ──────────────────────────────────────────────────
class CollectionDashboard:
    def __init__(self, symbol: str, data_dir: str) -> None:
        self.symbol = symbol.upper()
        self.data_dir = data_dir
        
        self.start_time = time.time()
        self.trades_processed = 0
        self.total_volume = 0.0
        self.depth_events = 0
        
        self.recent_trades: list[TradeTick] = []
        self.chart = TerminalCandleChart(width=55, height=12)
        
        # Instantiate Collector without C++ engine mirror for clean CLI operation
        self.collector = Collector(
            symbol=self.symbol,
            data_dir=self.data_dir,
            enable_engine_mirror=False,
            flush_every=100,
            heartbeat_s=10,
        )
        
        # Hook callbacks
        self.collector.on_book_update(self._on_book_update)
        self.collector.on_trade(self._on_trade)

    def _on_book_update(self, event: dict) -> None:
        self.depth_events += 1

    def _on_trade(self, tick: TradeTick) -> None:
        self.trades_processed += 1
        self.total_volume += tick.qty
        self.recent_trades.append(tick)
        if len(self.recent_trades) > 10:
            self.recent_trades.pop(0)
            
        # Feed chart
        self.chart.add_trade(tick.price, tick.qty, not tick.is_buyer_maker)

    def generate_layout(self) -> Layout:
        # Layout definition
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )
        
        # Split main section horizontally
        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1)
        )
        
        # Split right section vertically
        layout["right"].split(
            Layout(name="chart", ratio=1),
            Layout(name="trades", size=10)
        )
        
        # 1. Header Rendering — use Text with markup=True (Text.assemble treats 2nd arg
        #    as a *style*, not markup, so raw markup strings would appear literally there)
        uptime = int(time.time() - self.start_time)
        hrs, mins, secs = uptime // 3600, (uptime % 3600) // 60, uptime % 60

        # Postgres pill
        pg = self.collector._pg_writer
        if pg.is_enabled:
            db_pill = Text("● Connected", style="bold green")
        elif pg.last_error:
            db_pill = Text(f"✗ {pg.last_error[:18]}", style="bold red")
        else:
            db_pill = Text("○ Disabled", style="bold yellow")

        # WS pill
        ws_stat = self.collector.ws_status
        is_synced = self.collector.depth_mgr.is_synced
        if is_synced:
            ws_pill = Text("● Live", style="bold green")
        elif ws_stat == "Connected":
            ws_pill = Text("◑ Syncing...", style="bold yellow")
        elif ws_stat == "Connecting...":
            ws_pill = Text("◌ Connecting...", style="bold cyan")
        elif ws_stat == "Initializing...":
            ws_pill = Text("◌ Initializing...", style="bold cyan")
        else:
            err_clip = self.collector.ws_error[:18] if self.collector.ws_error else "Disconnected"
            ws_pill = Text(f"✗ {err_clip}", style="bold red")

        header_text = Text()
        header_text.append("LOB-X  │  ", style="bold green")
        header_text.append(f"{self.symbol}  │  ", style="bold")
        header_text.append(f"{hrs:02d}:{mins:02d}:{secs:02d}  │  ", style="bold white")
        header_text.append("WS: ", style="dim")
        header_text.append_text(ws_pill)
        header_text.append("  │  DB: ", style="dim")
        header_text.append_text(db_pill)

        layout["header"].update(Panel(Align.center(header_text), border_style="green"))

        # 2. Left Panel: Order Book depth
        book = self.collector.depth_mgr.book
        bb = book.best_bid()
        ba = book.best_ask()

        book_table = Table(title="Live Order Book L2", show_header=True, expand=True, box=None)
        book_table.add_column("Side", justify="center", width=5)
        book_table.add_column("Price ($)", justify="right")
        book_table.add_column("Size", justify="right")
        book_table.add_column("Depth", justify="left")

        if bb is not None and ba is not None and is_synced:
            asks_view = list(book.asks.items())[:5]
            bids_view = list(book.bids.items())[-5:]

            all_qtys = [q for _, q in asks_view + bids_view]
            max_qty = max(all_qtys) if all_qtys else 1.0

            for price, qty in reversed(asks_view):
                bar_len = int(qty / max_qty * 10)
                bar = "█" * max(bar_len, 1)
                book_table.add_row("[red]ASK[/red]", f"{price:,.2f}", f"{qty:.4f}", f"[red]{bar}[/red]")

            spread = ba[0] - bb[0]
            book_table.add_row("─" * 4, f"Spread: {spread:.2f}", "─" * 6, "─" * 10, style="dim")

            for price, qty in reversed(bids_view):
                bar_len = int(qty / max_qty * 10)
                bar = "█" * max(bar_len, 1)
                book_table.add_row("[green]BID[/green]", f"{price:,.2f}", f"{qty:.4f}", f"[green]{bar}[/green]")
        else:
            # Not yet synced — show clean status
            ws_err = self.collector.ws_error or ""
            status_txt = Text(justify="center")
            if ws_stat == "Initializing...":
                status_txt.append("◌  Initializing collector…\n", style="bold cyan")
                status_txt.append("Connecting to PostgreSQL…", style="dim")
            elif ws_stat == "Connecting...":
                status_txt.append("◌  Connecting to Binance…\n", style="bold cyan")
                status_txt.append("Streaming depth & trades…", style="dim")
            elif ws_stat == "Connected":
                status_txt.append("◑  WebSocket connected!\n", style="bold yellow")
                status_txt.append("Syncing L2 order book snapshot…", style="yellow")
            else:
                err_short = ws_err[:35] if ws_err else "Disconnected"
                status_txt.append("✗  WebSocket Disconnected\n", style="bold red")
                status_txt.append(f"{err_short}\n\n", style="red")
                status_txt.append("Reconnecting automatically…", style="dim")

            layout["left"].update(Panel(
                Align.center(status_txt, vertical="middle"),
                border_style="cyan", title="Order Book Depth"
            ))
            # Fill remaining panels and return — book_table was never used
            layout["chart"].update(Panel(self.chart.render(), border_style="magenta", title="Price Candles (10s buckets)"))
            _tt = Table(show_header=True, expand=True, box=None)
            _tt.add_column("Time", justify="center", width=8)
            _tt.add_column("Side", justify="center", width=5)
            _tt.add_column("Price ($)", justify="right")
            _tt.add_column("Size", justify="right")
            layout["trades"].update(Panel(_tt, border_style="yellow", title="Live Trade Feed"))
            layout["footer"].update(Panel(Text(
                f"Stats: {self.depth_events:,} Depth events │ "
                f"{self.trades_processed:,} Trades │ {self.total_volume:.4f} BTC Volume\n"
                "Press Ctrl+C to stop collection and return to CLI Menu.",
                justify="center", style="bold white"
            ), border_style="blue"))
            return layout

        layout["left"].update(Panel(book_table, border_style="cyan", title="Order Book Depth"))
        
        # 3. Chart Panel
        layout["chart"].update(Panel(self.chart.render(), border_style="magenta", title="Price Candles (10s buckets)"))
        
        # 4. Trades Panel
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
        
        # 5. Footer Panel
        footer_text = Text(
            f"Stats: {self.depth_events:,} Depth events │ {self.trades_processed:,} Trades │ {self.total_volume:.4f} BTC Volume\n"
            f"Press Ctrl+C to stop collection and return to CLI Menu.",
            justify="center",
            style="bold white"
        )
        layout["footer"].update(Panel(footer_text, border_style="blue"))
        
        return layout

    async def run(self) -> None:
        """Run the collection loop with terminal refresh and background file logging."""
        # Set up permanent file logging so real logs and proof are always saved
        os.makedirs("logs", exist_ok=True)
        log_file = "logs/collector.log"
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)

        self._run_error: str | None = None

        ws_task = asyncio.create_task(self.collector.run())
        await asyncio.sleep(0)  # yield so ws_task starts its first iteration

        try:
            with Live(self.generate_layout(), screen=True, auto_refresh=False) as live:
                while True:
                    await asyncio.sleep(0.5)
                    if ws_task.done() and not ws_task.cancelled():
                        try:
                            ws_task.result()
                        except Exception as exc:
                            self._run_error = f"Collector error: {exc}"
                            logging.error("Collector task failed", exc_info=True)
                    try:
                        live.update(self.generate_layout())
                    except Exception as exc:
                        logging.debug("Layout update skipped: %s", exc)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        except Exception as exc:
            self._run_error = f"Dashboard error: {exc}"
            logging.error("Dashboard error", exc_info=True)
        finally:
            self.collector.stop()
            ws_task.cancel()
            try:
                await ws_task
            except (asyncio.CancelledError, Exception):
                pass
            await self.collector.close_async()
            root_logger.removeHandler(file_handler)
            file_handler.close()


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

        elif cmd == "collect":
            symbol = args[0] if args else "BTCUSDT"
            data_dir = args[1] if len(args) > 1 else "data/raw"
            self.cmd_collect(symbol, data_dir)

        elif cmd == "status":
            self.cmd_status()

        else:
            CONSOLE.print(f"[bold red]Unknown command:[/bold red] '{cmd}'. Type 'help' for commands.")

    def cmd_help(self) -> None:
        table = Table(title="Available CLI Commands", show_header=True, header_style="bold cyan")
        table.add_column("Command", style="bold yellow")
        table.add_column("Description")

        table.add_row("collect [symbol] [dir]", "Start real-time market data collection with visual dashboard. Symbol defaults to BTCUSDT.")
        table.add_row("status", "Show local storage statistics and Postgres database status.")
        table.add_row("help / ?", "Show this command listing.")
        table.add_row("exit / quit / q", "Gracefully terminate CLI session.")

        CONSOLE.print(table)

    def cmd_collect(self, symbol: str, data_dir: str) -> None:
        CONSOLE.print(f"\n[bold green]Starting Data Collection for {symbol} in {data_dir}...[/bold green]")
        dashboard = CollectionDashboard(symbol, data_dir)

        try:
            asyncio.run(dashboard.run())
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            CONSOLE.print(f"[bold red]Collector stopped unexpectedly:[/bold red] {exc}")

        if dashboard._run_error:
            CONSOLE.print(f"\n[bold red]Collector stopped with issue:[/bold red] {dashboard._run_error}")
            CONSOLE.print("See [bold cyan]logs/collector.log[/bold cyan] for complete details.\n")
        else:
            CONSOLE.print("\n[bold green]✓ Data collection finished and flushed safely.[/bold green]\n")

    def cmd_status(self) -> None:
        CONSOLE.print("\n[bold cyan]LOB-X System Status[/bold cyan]")

        data_dir = Path("data/raw")
        parquet_files = list(data_dir.glob("*.parquet"))
        num_files = len(parquet_files)
        total_size_bytes = sum(f.stat().st_size for f in parquet_files)

        CONSOLE.print(f"  Local data directory : [white]{data_dir.absolute()}[/white]")
        CONSOLE.print(f"  Parquet files count  : [white]{num_files} files[/white]")
        CONSOLE.print(f"  Total Parquet size   : [white]{total_size_bytes / (1024*1024):.2f} MB[/white]")

        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            masked_url = db_url.split("@")[-1] if "@" in db_url else db_url
            CONSOLE.print(f"  PostgreSQL URL       : [white]...@{masked_url}[/white]")
            CONSOLE.print(f"  PostgreSQL Status    : [green]Configured[/green]")
        else:
            CONSOLE.print(f"  PostgreSQL Status    : [yellow]Disabled (DATABASE_URL not set)[/yellow]")
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

    # Windows: use SelectorEventLoop to avoid SSL cleanup errors (ProactorLoop +
    # asyncpg teardown raises "RuntimeError: Event loop is closed" on GC).
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    cli = LobxCLI()
    cli.loop()

