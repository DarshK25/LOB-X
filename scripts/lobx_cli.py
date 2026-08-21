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
class TerminalCandleChart:
    """
    Renders simple ASCII/Unicode candlestick charts.
    """
    def __init__(self, width: int = 50, height: int = 10) -> None:
        self.width = width
        self.height = height
        # Stores historical (open, high, low, close, volume, is_up) candles
        self.candles: list[tuple[float, float, float, float, float, bool]] = []

    def add_trade(self, price: float, qty: float, is_buy: bool) -> None:
        """Add a trade to the latest candle bucket."""
        # 5-second candle windows
        now_bucket = int(time.time() / 5)
        
        if not self.candles:
            # First candle
            self.candles.append((price, price, price, price, qty, is_buy))
            return
            
        # Check if latest candle is in the current bucket
        # (Simply group by size for simplicity if precise timestamp is not available)
        # For simplicity, we just keep the last 50 candles, updating the latest one
        # until a time interval has elapsed.
        last_o, last_h, last_l, last_c, last_v, last_side = self.candles[-1]
        
        # New candle every 3 seconds for fast visual updates
        if len(self.candles) > 0 and int(time.time()) % 3 == 0:
            # Close current and start new
            self.candles.append((price, price, price, price, qty, is_buy))
        else:
            # Update latest candle
            self.candles[-1] = (
                last_o,
                max(last_h, price),
                min(last_l, price),
                price,
                last_v + qty,
                is_buy
            )
            
        # Constrain to window width
        if len(self.candles) > self.width:
            self.candles.pop(0)

    def render(self) -> Text:
        """Draw candles in the terminal using rich Text."""
        if not self.candles:
            return Text("Waiting for trades...", style="yellow")
            
        # Calculate min/max price range for vertical scaling
        prices = [p for c in self.candles for p in (c[1], c[2])] # Highs and Lows
        if not prices:
            return Text("Chart Empty", style="dim")
            
        min_p = min(prices)
        max_p = max(prices)
        p_range = max_p - min_p if max_p != min_p else 1.0
        
        # Grid of characters
        grid = [[" " for _ in range(len(self.candles))] for _ in range(self.height)]
        colors = []
        
        for col, (o, h, l, c, v, is_buy) in enumerate(self.candles):
            # Normalize high, low, open, close to grid rows (0 is bottom, height-1 is top)
            row_h = int((h - min_p) / p_range * (self.height - 1))
            row_l = int((l - min_p) / p_range * (self.height - 1))
            row_o = int((o - min_p) / p_range * (self.height - 1))
            row_c = int((c - min_p) / p_range * (self.height - 1))
            
            # Wicks (Low to High)
            for r in range(row_l, row_h + 1):
                grid[r][col] = "│"
                
            # Body (Open to Close)
            body_start = min(row_o, row_c)
            body_end = max(row_o, row_c)
            for r in range(body_start, body_end + 1):
                grid[r][col] = "█"
                
            colors.append("green" if c >= o else "red")
            
        # Build the final Rich Text line by line (top to bottom)
        out = Text()
        for r in reversed(range(self.height)):
            for col in range(len(self.candles)):
                char = grid[r][col]
                color = colors[col]
                # If wick, make it dimmer or standard color
                out.append(char, style=color)
            out.append("\n")
            
        # Append price scale markers at the bottom
        out.append(f"Price Range: ${min_p:,.2f} – ${max_p:,.2f}", style="dim")
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
        self.chart = TerminalCandleChart(width=60, height=8)
        
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
            db_pill = Text(f"✗ Error: {pg.last_error[:30]}", style="bold red")
        else:
            db_pill = Text("○ No URL", style="bold yellow")

        # WS pill
        ws_stat = self.collector.ws_status
        is_synced = self.collector.depth_mgr.is_synced
        if is_synced:
            ws_pill = Text("● Live", style="bold green")
        elif ws_stat == "Connected":
            ws_pill = Text("◑ Syncing book...", style="bold yellow")
        elif ws_stat == "Connecting...":
            ws_pill = Text("◌ Connecting...", style="bold cyan")
        else:
            err_clip = self.collector.ws_error[:28] if self.collector.ws_error else "No route"
            ws_pill = Text(f"✗ {err_clip}", style="bold red")

        header_text = Text()
        header_text.append("LOB-X Data Collector Dashboard  │  ", style="bold green")
        header_text.append("Symbol: ", style="dim")
        header_text.append(f"{self.symbol}  │  ", style="bold")
        header_text.append("Uptime: ", style="dim")
        header_text.append(f"{hrs:02d}:{mins:02d}:{secs:02d}  │  ", style="bold")
        header_text.append("Binance WS: ", style="dim")
        header_text.append_text(ws_pill)
        header_text.append("  │  Postgres: ", style="dim")
        header_text.append_text(db_pill)

        layout["header"].update(Panel(Align.center(header_text), border_style="green"))

        # 2. Left Panel: Order Book depth
        book = self.collector.depth_mgr.book
        bb = book.best_bid()
        ba = book.best_ask()

        book_table = Table(title="Live Order Book L2", show_header=True, expand=True, box=None)
        book_table.add_column("Type", justify="center")
        book_table.add_column("Price ($)", justify="right")
        book_table.add_column("Size (Qty)", justify="right")
        book_table.add_column("Depth Visualizer", justify="left")

        if bb is not None and ba is not None:
            asks_view = list(book.asks.items())[:5]
            bids_view = list(book.bids.items())[-5:]

            all_qtys = [q for _, q in asks_view + bids_view]
            max_qty = max(all_qtys) if all_qtys else 1.0

            for price, qty in reversed(asks_view):
                bar_len = int(qty / max_qty * 12)
                bar = "█" * max(bar_len, 1)
                book_table.add_row("[red]ASK[/red]", f"{price:.2f}", f"{qty:.5f}", f"[red]{bar}[/red]")

            spread = ba[0] - bb[0]
            book_table.add_row("─" * 4, f"Spread: {spread:.2f}", "─" * 9, "─" * 15, style="dim")

            for price, qty in reversed(bids_view):
                bar_len = int(qty / max_qty * 12)
                bar = "█" * max(bar_len, 1)
                book_table.add_row("[green]BID[/green]", f"{price:.2f}", f"{qty:.5f}", f"[green]{bar}[/green]")
        else:
            # Show granular status so user isn't left guessing
            if ws_stat == "Disconnected":
                err_clip = self.collector.ws_error[:50] if self.collector.ws_error else "Cannot reach Binance"
                msg = f"[red]✗ WebSocket error: {err_clip}[/red]"
            elif ws_stat == "Connecting...":
                msg = "[cyan]◌ Connecting to Binance WebSocket...[/cyan]"
            else:
                # Connected but REST snapshot still in-flight
                msg = "[yellow]◑ WebSocket live — fetching order book snapshot from Binance REST API...[/yellow]"
            book_table.add_row(msg, "", "", "")

        layout["left"].update(Panel(book_table, border_style="cyan", title="Order Book Depth"))
        
        # 3. Chart Panel
        layout["chart"].update(Panel(self.chart.render(), border_style="magenta", title="Real-Time Price Candles (5s)"))
        
        # 4. Trades Panel
        trades_table = Table(show_header=True, expand=True, box=None)
        trades_table.add_column("Time", justify="center")
        trades_table.add_column("Taker Side", justify="center")
        trades_table.add_column("Price ($)", justify="right")
        trades_table.add_column("Size (BTC)", justify="right")
        
        for t in reversed(self.recent_trades):
            time_str = datetime.fromtimestamp(t.event_time / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            side_color = "red" if t.is_buyer_maker else "green"
            side_str = "SELL" if t.is_buyer_maker else "BUY"
            trades_table.add_row(
                time_str,
                f"[{side_color}]{side_str}[/{side_color}]",
                f"{t.price:.2f}",
                f"{t.qty:.5f}"
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
        """Run the collection loop with terminal refresh."""
        # Keep INFO level temporarily to debug the sync issue
        logging.getLogger("lobx").setLevel(logging.INFO)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("websockets").setLevel(logging.WARNING)
        
        # Start collector tasks
        ws_task = asyncio.create_task(self.collector.run())
        
        # Wait for initial connection and sync
        # Print status updates while waiting
        CONSOLE.print("[cyan]Waiting for WebSocket connection and order book sync...[/cyan]")
        max_wait = 10  # Wait up to 10 seconds for sync
        waited = 0
        while waited < max_wait:
            await asyncio.sleep(0.5)
            waited += 0.5
            
            if self.collector.depth_mgr.is_synced:
                CONSOLE.print("[green]✓ Order book synced![/green]")
                break
                
            if ws_task.done():
                try:
                    ws_task.result()
                except Exception as e:
                    CONSOLE.print(f"[red]✗ Collector task failed: {e}[/red]")
                    raise
                    
        if not self.collector.depth_mgr.is_synced:
            CONSOLE.print("[yellow]⚠ Sync taking longer than expected, starting display anyway...[/yellow]")
        
        # Suppress logs now that we're starting the visual display
        logging.getLogger("lobx").setLevel(logging.ERROR)
        
        # Run live terminal updater with periodic refresh
        with Live(self.generate_layout(), screen=True, refresh_per_second=4) as live:
            try:
                while True:
                    # Sleep for a reasonable interval
                    await asyncio.sleep(0.25)
                    
                    # Check if ws_task crashed
                    if ws_task.done():
                        try:
                            ws_task.result()
                        except Exception as e:
                            # Task failed - show error and exit
                            raise
                    
                    # Update display
                    live.update(self.generate_layout())
                    
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            finally:
                # Cancel and cleanup collector tasks
                self.collector.stop()
                ws_task.cancel()
                try:
                    await ws_task
                except asyncio.CancelledError:
                    pass
                    
                # Graceful flushing and closing of Parquet and Postgres
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
            CONSOLE.print("Run with the workspace interpreter to see full diagnostics:")
            CONSOLE.print("  .VENV\\Scripts\\python.exe scripts\\lobx_cli.py")
            
        CONSOLE.print("\n[bold green]✓ Data collection finished and flushed safely.[/bold green]\n")

    def cmd_status(self) -> None:
        CONSOLE.print("\n[bold cyan]LOB-X System Status[/bold cyan]")
        
        # Read local directories
        data_dir = Path("data/raw")
        parquet_files = list(data_dir.glob("*.parquet"))
        num_files = len(parquet_files)
        total_size_bytes = sum(f.stat().st_size for f in parquet_files)
        
        CONSOLE.print(f"  Local data directory : [white]{data_dir.absolute()}[/white]")
        CONSOLE.print(f"  Parquet files count  : [white]{num_files} files[/white]")
        CONSOLE.print(f"  Total Parquet size   : [white]{total_size_bytes / (1024*1024):.2f} MB[/white]")
        
        # Postgres Status
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
        
    cli = LobxCLI()
    cli.loop()
