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
from collections import deque
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

# ── Microstructure & Mathematical Model Helpers ──────────────────────────────
def order_flow_imbalance(bid_vol: float, ask_vol: float) -> float:
    """[-1.0, +1.0]; +1 = pure buy pressure, -1 = pure sell pressure."""
    total = bid_vol + ask_vol
    return (bid_vol - ask_vol) / total if total > 0 else 0.0

def microprice(best_bid: float, best_ask: float, bid_vol: float, ask_vol: float) -> float:
    """Stoikov (2018) volume-weighted micro-price estimator."""
    total = bid_vol + ask_vol
    if total == 0:
        return (best_bid + best_ask) / 2.0
    return best_bid * (ask_vol / total) + best_ask * (bid_vol / total)

def reservation_price(mid: float, inventory: float, gamma: float, sigma: float, time_remaining: float) -> float:
    """Avellaneda-Stoikov (2008) reservation price."""
    return mid - inventory * gamma * (sigma ** 2) * time_remaining

def optimal_spread(gamma: float, sigma: float, time_remaining: float, kappa: float) -> float:
    """Avellaneda-Stoikov optimal half-spread calculation."""
    return gamma * (sigma ** 2) * time_remaining + (2.0 / gamma) * math.log(1.0 + gamma / kappa)

def as_quotes(mid: float, inventory: float, gamma: float, sigma: float, time_remaining: float, kappa: float) -> tuple[float, float]:
    """Compute optimal Avellaneda-Stoikov Bid/Ask quotes."""
    r = reservation_price(mid, inventory, gamma, sigma, time_remaining)
    spread = optimal_spread(gamma, sigma, time_remaining, kappa)
    return r - spread / 2.0, r + spread / 2.0

class HawkesIntensityGauge:
    """
    Real-time Hawkes process intensity gauge for order flow arrival.
    λ(t) = μ + Σ α * exp(-β * (t - t_i))
    """
    def __init__(self, mu: float = 0.5, alpha: float = 0.8, beta: float = 2.0, window_s: float = 30.0) -> None:
        self.mu = mu
        self.alpha = alpha
        self.beta = beta
        self.window_s = window_s
        self.events: deque[float] = deque()

    def on_trade(self) -> None:
        self.events.append(time.time())

    def intensity(self) -> float:
        now = time.time()
        while self.events and (now - self.events[0]) > self.window_s:
            self.events.popleft()
        return self.mu + sum(self.alpha * math.exp(-self.beta * (now - t)) for t in self.events)

    def state(self) -> str:
        i = self.intensity()
        if i > self.mu * 4.0:
            return "CASCADE"
        if i > self.mu * 2.0:
            return "ELEVATED"
        return "NORMAL"

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
    Renders clean, time-bucketed ASCII/Unicode candlestick charts with true 5-second intervals.
    """
    def __init__(self, width: int = 50, height: int = 8, bucket_seconds: float = 5.0) -> None:
        self.width = width
        self.height = height
        self.bucket_seconds = bucket_seconds
        self.current_bucket: int | None = None
        # Stores historical candles: [open, high, low, close, volume, is_up]
        self.candles: list[list[Any]] = []

    def add_trade(self, price: float, qty: float, is_buy: bool) -> None:
        """Add a trade to the deterministic time bucket."""
        bucket = int(time.time() // self.bucket_seconds)
        
        if self.current_bucket != bucket or not self.candles:
            self.candles.append([price, price, price, price, qty, is_buy])
            self.current_bucket = bucket
        else:
            o, h, l, c, v, _ = self.candles[-1]
            self.candles[-1] = [o, max(h, price), min(l, price), price, v + qty, is_buy]
            
        # Constrain to window width
        if len(self.candles) > self.width:
            self.candles.pop(0)

    def render(self) -> Text:
        """Draw true time-bucketed candlestick bars in the terminal."""
        if not self.candles:
            return Text("Waiting for trades to form 5s candles...", style="yellow")
            
        # Calculate min/max price range for vertical scaling
        highs = [float(c[1]) for c in self.candles]
        lows = [float(c[2]) for c in self.candles]
        min_p = min(lows)
        max_p = max(highs)
        p_range = max_p - min_p if max_p != min_p else 1.0
        
        # Grid of characters: height rows × len(candles) columns
        n_cols = len(self.candles)
        grid = [[" " for _ in range(n_cols)] for _ in range(self.height)]
        colors = []
        
        for col, (o, h, l, c, v, is_buy) in enumerate(self.candles):
            # Normalize to grid rows (0 is bottom, height-1 is top)
            row_h = int(round((h - min_p) / p_range * (self.height - 1)))
            row_l = int(round((l - min_p) / p_range * (self.height - 1)))
            row_o = int(round((o - min_p) / p_range * (self.height - 1)))
            row_c = int(round((c - min_p) / p_range * (self.height - 1)))
            
            # Clamp bounds
            row_h = max(0, min(self.height - 1, row_h))
            row_l = max(0, min(self.height - 1, row_l))
            row_o = max(0, min(self.height - 1, row_o))
            row_c = max(0, min(self.height - 1, row_c))
            
            # 1. Draw Wicks (Low to High)
            for r in range(row_l, row_h + 1):
                grid[r][col] = "│"
                
            # 2. Draw Body (Open to Close)
            body_start = min(row_o, row_c)
            body_end = max(row_o, row_c)
            if body_start == body_end:
                grid[body_start][col] = "─"  # Doji
            else:
                for r in range(body_start, body_end + 1):
                    grid[r][col] = "█"
                
            # Bullish (Green) or Bearish (Red)
            colors.append("bold green" if c >= o else "bold red")
            
        # Build the final Rich Text line by line (top to bottom)
        out = Text()
        for r in reversed(range(self.height)):
            for col in range(n_cols):
                char = grid[r][col]
                color = colors[col]
                # If space, keep default, otherwise apply candle color
                if char == " ":
                    out.append(" ")
                else:
                    out.append(char, style=color)
            out.append("\n")
            
        # Price range and latest close footer
        latest_close = float(self.candles[-1][3])
        out.append(f"Last: {latest_close:.2f} │ Range: {min_p:.2f} – {max_p:.2f} (5s candles)", style="dim cyan")
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
        self.hawkes_gauge = HawkesIntensityGauge(mu=0.5, alpha=0.8, beta=2.0, window_s=30.0)
        
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
            
        # Feed chart & Hawkes intensity gauge
        self.chart.add_trade(tick.price, tick.qty, not tick.is_buyer_maker)
        self.hawkes_gauge.on_trade()

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
        
        # Split left section vertically into order book and quant engine
        layout["left"].split(
            Layout(name="book", ratio=1),
            Layout(name="quant", size=11)
        )
        
        # Split right section vertically into price chart and trade feed
        layout["right"].split(
            Layout(name="chart", ratio=1),
            Layout(name="trades", size=11)
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

        # 2. Left-Top Panel: Order Book depth
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

        layout["book"].update(Panel(book_table, border_style="cyan", title="Order Book Depth"))

        # Hawkes state calculation for visual tie-in
        h_state = self.hawkes_gauge.state()
        h_val = self.hawkes_gauge.intensity()
        is_cascade = (h_state == "CASCADE")

        # 3. Left-Bottom Panel: Quantitative Microstructure Engine Panel
        quant_table = Table(show_header=False, expand=True, box=None, padding=(0, 1))
        quant_table.add_column("Metric", style="bold cyan", justify="left")
        quant_table.add_column("Output", justify="right")

        if bb is not None and ba is not None:
            bid_p, bid_v = bb[0], bb[1]
            ask_p, ask_v = ba[0], ba[1]
            mid = (bid_p + ask_p) / 2.0
            obi = order_flow_imbalance(bid_v, ask_v)
            m_price = microprice(bid_p, ask_p, bid_v, ask_v)
            delta = m_price - mid

            # Avellaneda-Stoikov quotes (starting calibration parameters)
            gamma, sigma, time_rem, kappa = 0.1, 1.0, 1.0, 1.5
            as_bid, as_ask = as_quotes(mid, 0.0, gamma, sigma, time_rem, kappa)

            obi_style = "bold green" if obi > 0 else ("bold red" if obi < 0 else "dim")
            delta_style = "bold green" if delta > 0 else ("bold red" if delta < 0 else "dim")
            
            if h_state == "CASCADE":
                h_style = "bold red"
            elif h_state == "ELEVATED":
                h_style = "bold yellow"
            else:
                h_style = "bold green"

            quant_table.add_row("OBI Ratio", f"[{obi_style}]{obi:+.2f}[/{obi_style}]")
            quant_table.add_row("Micro-price", f"{m_price:.2f}")
            quant_table.add_row("Micro−Mid Δ", f"[{delta_style}]{delta:+.3f}[/{delta_style}]")
            quant_table.add_row("AS Bid Quote", f"{as_bid:.2f}")
            quant_table.add_row("AS Ask Quote", f"{as_ask:.2f}")
            quant_table.add_row("Hawkes λ", f"[{h_style}]{h_val:.2f} ({h_state})[/{h_style}]")
        else:
            quant_table.add_row("Status", "[yellow]Syncing order book...[/yellow]")

        layout["quant"].update(Panel(quant_table, border_style="magenta", title="Quantitative Microstructure Engine"))
        
        # 4. Right-Top Panel: Chart Panel
        layout["chart"].update(Panel(self.chart.render(), border_style="magenta", title="Real-Time Price Candles (5s)"))

        # 5. Right-Bottom Panel: Trades Panel
        trades_title = "Live Trade Feed [bold red](CASCADE)[/bold red]" if is_cascade else "Live Trade Feed"
        trades_table = Table(show_header=True, expand=True, box=None)
        trades_table.add_column("Time", justify="center")
        trades_table.add_column("Side", justify="center")
        trades_table.add_column("Price ($)", justify="right")
        trades_table.add_column("Size", justify="right")
        
        for t in reversed(self.recent_trades):
            time_str = datetime.fromtimestamp(t.event_time / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            side_color = "red" if t.is_buyer_maker else "green"
            side_str = "SELL" if t.is_buyer_maker else "BUY"
            row_style = "bold yellow" if is_cascade else None
            trades_table.add_row(
                time_str,
                f"[{side_color}]{side_str}[/{side_color}]",
                f"{t.price:.2f}",
                f"{t.qty:.4f}",
                style=row_style
            )
            
        layout["trades"].update(Panel(trades_table, border_style="yellow" if not is_cascade else "red", title=trades_title))
        
        # 6. Footer Panel
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
