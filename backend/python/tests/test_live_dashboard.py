"""Real dashboard rendering with an in-memory feed, never a network connection."""
import asyncio
import importlib.util
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from lobx.market_data.depth_stream_manager import LocalOrderBook
from lobx.market_data.trade_stream import TradeTick
from lobx.terminal.dashboard import LiveDashboard


class MemoryFeed:
    def __init__(self):
        self.ws_status = "Connected"
        self.depth_mgr = SimpleNamespace(book=LocalOrderBook(), is_synced=True)
        self.book_callback = None
        self.trade_callback = None
        self.closed = False
        self.stopped = False

    def on_book_update(self, cb):
        self.book_callback = cb

    def on_trade(self, cb):
        self.trade_callback = cb

    async def run(self):
        return

    def stop(self):
        self.stopped = True

    async def close_async(self):
        self.closed = True


def build(monkeypatch, width=120, height=36, symbol="BTCUSDT", mode="candle"):
    monkeypatch.setattr("lobx.terminal.dashboard.time", SimpleNamespace(
        time=lambda: 1_700_000_010.0, monotonic=lambda: 100.0,
    ))
    feed = MemoryFeed()
    console = Console(file=StringIO(), width=width, height=height, color_system=None)
    dashboard = LiveDashboard(symbol, bucket_s=1, mode=mode, collector=feed, console=console)
    feed.depth_mgr.book.bids[100.0] = 3.0
    feed.depth_mgr.book.asks[100.02] = 1.0
    feed.book_callback({"E": 1_700_000_010_000})
    for idx in range(15):
        feed.trade_callback(TradeTick(1_700_000_000_000 + idx * 600, idx,
                                    100 + (idx % 5) * .01, .1 + idx * .01,
                                    bool(idx % 2), symbol))
    return dashboard, feed, console


def rendered(dashboard, console):
    console.print(dashboard.generate_layout())
    return console.file.getvalue()


@pytest.mark.parametrize("width,height", [(80, 24), (120, 30), (160, 45), (60, 20)])
def test_dashboard_fits_and_keeps_price_volume_signals_visible(monkeypatch, width, height):
    dashboard, _, console = build(monkeypatch, width, height)
    text = rendered(dashboard, console)
    lines = text.splitlines()
    assert len(lines) == height
    assert all(len(line) <= width for line in lines)
    assert "Vol (BTC)" in text
    assert "OBI" in text
    assert "Hawkes" in text
    assert "1s" in text
    assert "5s" not in text
    assert "session" in text.lower()


def test_callback_uses_exchange_time_and_ignores_duplicate_deliveries(monkeypatch):
    dashboard, feed, _ = build(monkeypatch)
    tick = TradeTick(1_700_000_009_900, 20, 100.03, .7, False, "BTCUSDT")
    feed.trade_callback(tick)
    feed.trade_callback(tick)
    assert dashboard.signals.snapshot(tick.event_time).trade_count == 16
    assert sum(c.volume for c in dashboard.chart.candles) == pytest.approx(3.25)
    assert dashboard.chart.candles[-1].bucket_start_ms == 1_700_000_009_000


def test_book_spread_shown_once_and_metrics_not_fabricated(monkeypatch):
    dashboard, _, console = build(monkeypatch)
    text = rendered(dashboard, console)
    assert text.lower().count("spread") == 1
    assert "0.02" in text
    assert "heuristic" in text.lower()
    assert "AS Bid" not in text
    assert "50%" not in text


def test_stale_or_disconnected_book_is_not_shown_as_live(monkeypatch):
    dashboard, feed, console = build(monkeypatch)
    feed.ws_status = "Disconnected"
    text = rendered(dashboard, console)
    assert "RECONNECT" in text
    assert "No stale quotes displayed" in text
    assert "Spread" not in text
    assert "L1 OBI +0.50" not in text
    console.file = StringIO()
    feed.ws_status = "Connected"
    monkeypatch.setattr("lobx.terminal.dashboard.time.monotonic", lambda: 110.0)
    text = rendered(dashboard, console)
    assert "STALE" in text


def test_eth_volume_units_and_line_title(monkeypatch):
    dashboard, _, console = build(monkeypatch, symbol="ETHUSDT", mode="line")
    text = rendered(dashboard, console)
    assert "Vol (ETH)" in text
    assert "BTC" not in text
    assert "LINE" in text


def test_feed_that_stops_does_not_leave_infinite_ui_loop(monkeypatch):
    dashboard, feed, _ = build(monkeypatch)
    asyncio.run(asyncio.wait_for(dashboard.run(), timeout=2))
    assert feed.closed and feed.stopped
    assert "stopped" in dashboard._run_error.lower()


@pytest.mark.parametrize("timeframe", ["0s", "--1s", "-1s", "0.0s", "2.5m", "0"])
def test_invalid_timeframe_is_rejected_before_launch(monkeypatch, timeframe):
    path = Path(__file__).resolve().parents[3] / "scripts" / "lobx_cli.py"
    spec = importlib.util.spec_from_file_location("dashboard_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module.LobxCLI, "cmd_live", lambda *args: calls.append(args))
    module.LobxCLI().run_command(f"live BTCUSDT {timeframe}")
    assert not calls


def test_empty_dashboard_does_not_fill_unavailable_metrics(monkeypatch):
    _dashboard, _feed, console = build(monkeypatch)
    empty = LiveDashboard("BTCUSDT", collector=MemoryFeed(), console=console)
    text = rendered(empty, console)
    assert "Waiting for live trades" in text
    assert "warming up" in text
    assert "50%" not in text
    assert "● LIVE" not in text


def test_quiet_high_price_market_does_not_get_dollars_of_artificial_padding():
    from lobx.terminal.candles import AdaptiveTerminalChart

    chart = AdaptiveTerminalChart(height=18)
    chart.add_trade(81700, .1, True, 1_700_000_000_000)
    chart.add_trade(81700.01, .1, True, 1_700_000_001_000)
    text = chart.render(80).plain
    # A one-cent observed range must not be crushed inside a multi-dollar scale.
    assert "81,703" not in text
    assert "81,696" not in text


def test_subcent_prices_and_small_offsets_keep_meaningful_precision(monkeypatch):
    from lobx.terminal.candles import AdaptiveTerminalChart
    from lobx.terminal.dashboard import price_text

    assert price_text(1.2345) != price_text(1.2346)
    assert price_text(.00012345) != price_text(.00012346)
    chart = AdaptiveTerminalChart(height=18)
    chart.add_trade(.00012345, 1, True, 1_700_000_000_000)
    chart.add_trade(.00012346, 1, True, 1_700_000_001_000)
    text = chart.render(80).plain
    assert "Last 0.00012346" in text
    dashboard, feed, console = build(monkeypatch, symbol="ETHBTC")
    feed.depth_mgr.book.bids.clear()
    feed.depth_mgr.book.asks.clear()
    feed.depth_mgr.book.bids[.03000001] = 3
    feed.depth_mgr.book.asks[.03000002] = 1
    feed.book_callback({"E": 1_700_000_010_000})
    text = rendered(dashboard, console)
    assert "+0.00000 BTC" not in text


def test_historical_correction_does_not_refresh_latest_chart_price(monkeypatch):
    dashboard, feed, console = build(monkeypatch)
    monkeypatch.setattr("lobx.terminal.dashboard.time.monotonic", lambda: 110.0)
    feed.book_callback({"E": 1_700_000_020_000})
    feed.trade_callback(TradeTick(1_700_000_001_000, 100, 100.01, .1, True, "BTCUSDT"))
    assert "last trade" in rendered(dashboard, console)


@pytest.mark.parametrize("host_skew", [-60, 60])
def test_clock_skew_does_not_freeze_activity_or_invalidate_fresh_book(monkeypatch, host_skew):
    dashboard, feed, console = build(monkeypatch)
    monkeypatch.setattr("lobx.terminal.dashboard.time.time", lambda: 1_700_000_010 + host_skew)
    tick = TradeTick(1_700_000_010_000, 100, 100.01, .1, False, "BTCUSDT")
    feed.trade_callback(tick)
    monkeypatch.setattr("lobx.terminal.dashboard.time.monotonic", lambda: 110.0)
    feed.book_callback({"E": 1_700_000_020_000})
    text = rendered(dashboard, console)
    assert "● LIVE" in text
    assert "0.50/s QUIET" in text


def test_collector_cancellation_waits_for_socket_context_cleanup():
    from lobx.market_data.collector import Collector

    async def scenario():
        entered = asyncio.Event()
        cleanup_finished = asyncio.Event()

        class ClosingSocket:
            async def run(self):
                try:
                    entered.set()
                    await asyncio.Future()
                finally:
                    await asyncio.sleep(.01)
                    cleanup_finished.set()

        collector = Collector(symbol="BTCUSDT", enable_storage=False, enable_engine_mirror=False)
        collector._ws = ClosingSocket()
        task = asyncio.create_task(collector.run())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleanup_finished.is_set()
        await collector.close_async()

    asyncio.run(scenario())


def test_book_bars_show_level_size_not_repeated_cumulative_wall(monkeypatch):
    dashboard, feed, console = build(monkeypatch)
    feed.depth_mgr.book.asks[100.03] = .001
    feed.depth_mgr.book.asks[100.04] = .00007
    feed.book_callback({"E": 1_700_000_010_000})
    snapshot = dashboard.signals.snapshot(dashboard._exchange_now_ms())
    console.print(dashboard._book_panel(snapshot, 13))
    text = console.file.getvalue()
    assert "per-level" in text
    assert "0.00007" in text
    tiny_row = next(row for row in text.splitlines() if "100.04" in row)
    assert "██" not in tiny_row


@pytest.mark.parametrize("scenario", ["quiet", "moving", "breakout"])
def test_preview_covers_realistic_quiet_and_moving_regimes(scenario):
    path = Path(__file__).resolve().parents[3] / "scripts" / "preview_terminal.py"
    spec = importlib.util.spec_from_file_location("terminal_preview_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    console = Console(file=StringIO(), width=160, height=42, color_system=None)
    console.print(module.demo_layout(console, scenario=scenario))
    text = console.file.getvalue()
    assert "DEMO" in text and "NOT live" in text
    assert "per-level" in text
    assert len(text.splitlines()) == 42
    if scenario == "quiet":
        assert "Quiet price" in text
        assert "Observed range 0.01" in text
        assert "sqrt" in text
    else:
        assert "Quiet price" not in text
