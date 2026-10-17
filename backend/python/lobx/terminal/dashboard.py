"""Storage-disabled live market workspace: observed trades, book, and signals."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from itertools import islice
from math import floor, isfinite, log10
from typing import Any

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lobx.market_data.collector import Collector
from lobx.market_data.trade_stream import TradeTick
from lobx.terminal.candles import AdaptiveTerminalChart
from lobx.terminal.signals import LiveMarketSignals, LiveSignalsSnapshot


def market_units(symbol: str) -> tuple[str, str]:
    """Infer familiar exchange pairs; unknown pairs use honest generic units."""
    for quote in ("FDUSD", "USDT", "USDC", "TUSD", "BUSD", "BTC", "ETH", "EUR", "USD", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[:-len(quote)], quote
    return "base", "quote"


def price_text(value: float) -> str:
    """Keep sub-cent pairs visible without a false universal dollar label."""
    if not value:
        return "0.00"
    if abs(value) < 1e-7 or abs(value) >= 1e9:
        return f"{value:.8g}"
    decimals = max(2, min(10, 8 - floor(log10(abs(value)))))
    text = f"{value:,.{decimals}f}"
    integer, fraction = text.split(".")
    return integer + "." + fraction.rstrip("0").ljust(2, "0")


def quantity_text(value: float) -> str:
    """Readable base quantities; do not turn small real levels into 7e-05."""
    if 0 < value < 1e-8:
        return f"{value:.4g}"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def size_bar(quantity: float, maximum: float, width: int = 7) -> str:
    """Linear per-level quantity; a dot marks amounts below raster resolution."""
    units = max(0, min(width * 8, round(quantity / maximum * width * 8)))
    full, partial = divmod(units, 8)
    return "█" * full + (" ▏▎▍▌▋▊▉"[partial] if partial else "") or "·"


class LiveDashboard:
    """One event-loop-owned market display; never places orders or records data."""

    STALE_SECONDS = 5.0

    def __init__(
        self, symbol: str, bucket_s: int = 5, mode: str = "candle", *,
        collector: Collector | None = None, console: Console | None = None,
    ) -> None:
        # Validate configuration before creating a collector or its resources.
        self.chart = AdaptiveTerminalChart(height=20, bucket_s=bucket_s, mode=mode)
        self.symbol = symbol.upper()
        self.bucket_s = bucket_s
        self.mode = mode
        self.base_asset, self.quote_asset = market_units(self.symbol)
        self.console = console if console is not None else Console()
        self.start_time = time.monotonic()
        self.signals = LiveMarketSignals()
        self.trades_processed = 0
        self.depth_events = 0
        self.recent_trades: deque[TradeTick] = deque(maxlen=20)
        self._book_received: float | None = None
        self._trade_received: float | None = None
        self._latest_trade_time = 0.0
        self._exchange_anchor: float | None = None
        self._exchange_received: float | None = None
        self._run_error: str | None = None
        self.collector = collector if collector is not None else Collector(
            symbol=self.symbol, enable_engine_mirror=False, enable_storage=False, heartbeat_s=10,
        )
        self.collector.on_book_update(self._on_book_update)
        self.collector.on_trade(self._on_trade)

    def _levels(self, depth: int = 12) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        book = self.collector.depth_mgr.book
        # SortedDict: bids ascend, asks ascend. Copy only visible levels.
        bids = [(p, book.bids[p]) for p in islice(reversed(book.bids), depth)]
        asks = [(p, book.asks[p]) for p in islice(book.asks, depth)]
        return bids, asks

    def _on_book_update(self, event: dict[str, Any]) -> None:
        self.depth_events += 1
        self._book_received = time.monotonic()
        bids, asks = self._levels()
        self.signals.on_book(bids, asks, event.get("E", 0))
        self._advance_clock(event.get("E", 0))

    def _advance_clock(self, exchange_ms: float) -> None:
        """Anchor a monotonic feed clock; late corrections never rewind it."""
        if (isinstance(exchange_ms, (int, float)) and isfinite(exchange_ms)
                and exchange_ms > 0
                and (self._exchange_anchor is None or exchange_ms >= self._exchange_anchor)):
            self._exchange_anchor = float(exchange_ms)
            self._exchange_received = time.monotonic()

    def _exchange_now_ms(self) -> float:
        # Before the first event the model is at baseline. Do not seed its
        # irreversible evaluation cursor with a potentially skewed host clock.
        if self._exchange_anchor is None or self._exchange_received is None:
            return 1.0
        return self._exchange_anchor + max(0, time.monotonic() - self._exchange_received) * 1000

    def _trade_age(self, snapshot: LiveSignalsSnapshot) -> float | None:
        if self._trade_received is None or snapshot.last_trade_timestamp is None:
            return None
        return max(0.0, time.monotonic() - self._trade_received,
                   (self._exchange_now_ms() - snapshot.last_trade_timestamp) / 1000)

    def _on_trade(self, tick: TradeTick) -> None:
        if tick.symbol != self.symbol:
            return
        self.signals.on_trade(tick)
        snapshot = self.signals.snapshot(self._exchange_now_ms())
        if snapshot.trade_count == self.trades_processed:
            return  # Invalid or duplicate delivery: do not double-count candles/tape.
        self.chart.add_trade(tick.price, tick.qty, not tick.is_buyer_maker, tick.event_time)
        self.trades_processed = snapshot.trade_count
        if tick.event_time >= self._latest_trade_time:
            self._latest_trade_time = tick.event_time
            self._trade_received = time.monotonic()
        self._advance_clock(tick.event_time)
        self.recent_trades.append(tick)

    def _book_ready(self, snapshot: LiveSignalsSnapshot) -> bool:
        return (
            self.collector.ws_status == "Connected"
            and self.collector.depth_mgr.is_synced
            and self._book_received is not None
            and time.monotonic() - self._book_received <= self.STALE_SECONDS
            and snapshot.book_timestamp is not None
            and max(0, self._exchange_now_ms() - snapshot.book_timestamp) <= self.STALE_SECONDS * 1000
            and snapshot.mid is not None
        )

    def _feed_status(self, snapshot: LiveSignalsSnapshot) -> tuple[str, str]:
        status = self.collector.ws_status
        if status not in ("Connected", "Connecting...", "Initializing..."):
            return "RECONNECTING", "bold red"
        if status != "Connected":
            return "CONNECTING", "bold yellow"
        if self._book_received is not None and not self._book_ready(snapshot):
            return "STALE / RESYNC", "bold yellow"
        if not self.collector.depth_mgr.is_synced:
            return "SYNCING L2", "bold yellow"
        if self._book_ready(snapshot):
            return "LIVE", "bold green"
        return "WAITING FOR L2", "bold yellow"

    def _signals_panel(self, snapshot: LiveSignalsSnapshot, *, compact: bool) -> Panel:
        ready = self._book_ready(snapshot)
        obi = f"{snapshot.imbalance:+.2f}" if ready else "—"
        offset = f"{snapshot.microprice_offset:+.6g}" if ready else "—"
        vwap = price_text(snapshot.vwap) if snapshot.vwap is not None else "—"
        buys = f"{snapshot.buy_fraction:.0%}" if snapshot.buy_fraction is not None else "—"
        activity = (f"{snapshot.hawkes_intensity:.2f}/s {snapshot.hawkes_regime}"
                    if snapshot.trade_count else "warming up")
        content: Text | Table
        if compact:
            content = Text(no_wrap=True, overflow="crop")
            content.append(f"L1 OBI {obi}  Micro−Mid {offset} {self.quote_asset}\n", style="cyan")
            content.append(f"Hawkes λ {activity} (heuristic)\n", style="yellow")
            content.append(f"VWAP {vwap}  Buy {buys} · session", style="dim")
        else:
            content = Table.grid(expand=True, padding=(0, 1))
            content.add_column(style="dim", no_wrap=True)
            content.add_column(justify="right", no_wrap=True, overflow="crop")
            content.add_row("L1 OBI", obi)
            content.add_row("Micro−Mid", f"{offset} {self.quote_asset}")
            content.add_row("Session VWAP", vwap)
            content.add_row("Taker buy vol", buys)
            content.add_row("Hawkes λ", activity)
            content.add_row("λ / fixed μ", f"{snapshot.hawkes_ratio:.1f}× · heuristic" if snapshot.trade_count else "—")
            content.add_row("Model", "uncalibrated activity")
        return Panel(content, title="Microstructure", border_style="cyan", padding=(0, 1))

    def _book_panel(self, snapshot: LiveSignalsSnapshot, rows: int) -> Panel:
        if not self._book_ready(snapshot):
            return Panel(Text("Book unavailable / resyncing\nNo stale quotes displayed", style="dim"),
                         title="Order Book L2", border_style="bright_black")
        depth = max(1, (rows - 5) // 2)
        bids, asks = self._levels(depth)
        table = Table(box=None, expand=True, padding=(0, 1), show_header=True)
        table.add_column(self.quote_asset, justify="right", no_wrap=True)
        table.add_column(self.base_asset, justify="right", no_wrap=True)
        table.add_column("Size bar", justify="right", no_wrap=True)
        scale = max(q for side in (bids, asks) for _, q in side)
        for side, color in ((list(reversed(asks)), "red"), (bids, "green")):
            if color == "green":
                spread = f"{snapshot.spread:.8f}".rstrip("0").rstrip(".")
                table.add_row(Text(f"Spread {spread}", style="yellow"),
                              Text(self.quote_asset, style="dim"),
                              Text(f"{snapshot.spread_bps:.4f}bp", style="dim"))
            for p, q in side:
                bar = size_bar(q, scale)
                best = asks[0][0] if color == "red" else bids[0][0]
                price_style = f"bold {color}" if p == best else color
                table.add_row(Text(price_text(p), style=price_style), Text(quantity_text(q), style=color),
                              Text(bar, style=color))
        return Panel(table, title="Order Book L2", subtitle="per-level size · linear",
                     border_style="bright_black", padding=(0, 0))

    def _tape_panel(self, rows: int) -> Panel:
        table = Table(box=None, expand=True, padding=(0, 1))
        table.add_column("UTC", no_wrap=True, style="dim")
        table.add_column("Side", no_wrap=True)
        table.add_column(self.quote_asset, justify="right", no_wrap=True)
        table.add_column(self.base_asset, justify="right", no_wrap=True)
        for tick in list(reversed(self.recent_trades))[:max(0, rows - 3)]:
            color = "red" if tick.is_buyer_maker else "green"
            clock = datetime.fromtimestamp(tick.event_time / 1000, timezone.utc).strftime("%H:%M:%S")
            table.add_row(clock, Text(tick.taker_side.upper(), style=color),
                          price_text(tick.price), quantity_text(tick.qty))
        if not self.recent_trades:
            table.add_row("Waiting", "—", "—", "—")
        age = self._trade_age(self.signals.snapshot(self._exchange_now_ms()))
        title = "Trade Tape" if age is None or age <= self.STALE_SECONDS else f"Tape · {age:.0f}s idle"
        return Panel(table, title=title, border_style="bright_black", padding=(0, 0))

    def generate_layout(self) -> Layout:
        """Size chart and panels from the real viewport, including their borders."""
        width, height = self.console.size
        snapshot = self.signals.snapshot(self._exchange_now_ms())
        status, status_style = self._feed_status(snapshot)
        header = Text(no_wrap=True, overflow="crop")
        header.append(f"LOB-X  {self.symbol}", style="bold white")
        header.append(f"  ● {status}", style=status_style)
        if width >= 100:
            elapsed = int(time.monotonic() - self.start_time)
            header.append(f"  {elapsed // 60:02d}:{elapsed % 60:02d}  · Binance spot", style="dim")

        layout = Layout()
        layout.split_column(Layout(name="header", size=3), Layout(name="main", ratio=1),
                            Layout(name="footer", size=1))
        layout["header"].update(Panel(header, border_style="bright_black"))
        main_height = max(1, height - 4)
        wide = width >= 110 and height >= 26
        if wide:
            side_width = min(48, max(39, width // 3))
            layout["main"].split_row(Layout(name="chart", ratio=1),
                                     Layout(name="side", size=side_width))
            book_height = max(9, min(15, main_height - 15))
            tape_height = max(3, main_height - book_height - 9)
            layout["side"].split_column(Layout(name="book", size=book_height),
                                        Layout(name="signals", size=9), Layout(name="tape", ratio=1))
            layout["book"].update(self._book_panel(snapshot, book_height))
            layout["signals"].update(self._signals_panel(snapshot, compact=False))
            layout["tape"].update(self._tape_panel(tape_height))
            chart_width, chart_height = width - side_width, main_height
        else:
            # At 80x24 favor a readable price/volume pane, not four clipped boxes.
            layout["main"].split_column(Layout(name="chart", ratio=1),
                                        Layout(name="signals", size=5))
            layout["signals"].update(self._signals_panel(snapshot, compact=True))
            chart_width, chart_height = width, max(1, main_height - 5)
        chart_title = f"{self.symbol} · {self.mode.upper()} {self.bucket_s:g}s · {self.quote_asset}"
        age = self._trade_age(snapshot)
        if age is not None and age > self.STALE_SECONDS:
            chart_title += f" · last trade {age:.0f}s ago"
        layout["chart"].update(Panel(
            self.chart.render(max_cols=max(0, chart_width - 4), height=max(0, chart_height - 2),
                              volume_label=self.base_asset),
            title=chart_title, border_style="bright_black", padding=(0, 1),
        ))
        footer = Text(no_wrap=True, overflow="crop", style="dim")
        footer.append(f"Session {snapshot.trade_count:,} trades · {snapshot.total_volume:.4g} {self.base_asset}")
        footer.append("  │ in-memory · no orders  │ Ctrl+C")
        layout["footer"].update(footer)
        return layout

    async def run(self) -> None:
        """Render until interruption or transport completion, then always close."""
        self._run_error = None
        ws_task = asyncio.create_task(self.collector.run())
        try:
            with Live(self.generate_layout(), console=self.console, screen=False,
                      auto_refresh=False, vertical_overflow="crop") as live:
                while True:
                    await asyncio.sleep(0.25)
                    if ws_task.done():
                        if not ws_task.cancelled():
                            ws_task.result()  # Propagate actual transport errors to the CLI.
                        self._run_error = "Market feed stopped. Relaunch live to reconnect."
                        break
                    live.update(self.generate_layout(), refresh=True)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        except Exception as exc:  # noqa: BLE001 - UI boundary reports failures after cleanup.
            self._run_error = f"{type(exc).__name__}: {exc}"
        finally:
            self.collector.stop()
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)
            await self.collector.close_async()
