"""Live paper trading: every strategy on the same live feed, one account each.

Collector (L2 depth + trades) -> FeatureEngine -> MarketSimulator -> SimAccounts

The live path uses exactly the simulator and features of the backtests, so a
live paper session is an out-of-sample test of the same code. Sequence:
  1. warm-up: record top-of-book and trades for ``warmup_s`` seconds
  2. calibrate sigma, A, k, Hawkes and the fair-value model on the warm-up
     window (worker thread) and build the strategy suite from it
  3. paper-trade every strategy; refit Hawkes online every ``refit_s``
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Callable

import numpy as np

from lobx.analytics.calibration import calibrate
from lobx.analytics.performance import account_report
from lobx.backtest.data import MarketData
from lobx.execution.simulator import ExecutionConfig, MarketSimulator, SimAccount
from lobx.strategy.base import Strategy
from lobx.strategy.features import FeatureEngine, HawkesParams

if TYPE_CHECKING:
    from lobx.market_data.collector import Collector
    from lobx.market_data.trade_stream import TradeTick

logger = logging.getLogger(__name__)

SuiteFactory = Callable[[dict], list[Strategy]]


class LiveSimRunner:
    def __init__(self, collector: "Collector", build_suite: SuiteFactory, config: ExecutionConfig,
                 warmup_s: float = 120.0, refit_s: float = 60.0, decision_ms: int = 100,
                 venue: str = "Binance spot") -> None:
        self.collector = collector
        self.build_suite = build_suite
        self.config = config
        self.warmup_s = warmup_s
        self.refit_s = refit_s
        self.decision_ms = decision_ms
        self.venue = venue
        self.features = FeatureEngine(config.tick_size, event_history=20_000)
        self.sim: MarketSimulator | None = None
        self.accounts: list[SimAccount] = []
        self.calibration: dict | None = None
        self.status = "warming up"
        self.error: str | None = None
        self.started = time.monotonic()
        self.first_event_ms: int | None = None
        self.recorded_book: list[tuple] = []
        self.recorded_trades: list[tuple] = []
        self._mid_ts: list[int] = []
        self._mids: list[float] = []
        self._next_mid = 0
        self._last_l1: tuple | None = None
        self.recent_fills: deque[tuple[str, tuple]] = deque(maxlen=400)
        self._fill_counts: dict[str, int] = {}
        self._calibrating = False
        self._next_refit = 0.0
        collector.on_book_update(self._on_book)
        collector.on_trade(self._on_trade)

    # ── feed ────────────────────────────────────────────────────────────────
    def _on_book(self, event: dict) -> None:
        book = self.collector.depth_mgr.book
        bid, ask = book.best_bid(), book.best_ask()
        if bid is None or ask is None:
            return
        ts = int(event.get("E", 0))
        l1 = (bid[0], bid[1], ask[0], ask[1])
        if l1 == self._last_l1:
            return
        self._last_l1 = l1
        if self.first_event_ms is None:
            self.first_event_ms = ts
        if ts >= self._next_mid:
            self._mid_ts.append(ts)
            self._mids.append(0.5 * (bid[0] + ask[0]))
            self._next_mid = ts + 100
        if self.sim is None:
            self.features.on_book(ts, *l1)
            self.recorded_book.append((ts, *l1))
        else:
            self.sim.on_book(ts, *l1)
            self._collect_fills()

    def _on_trade(self, tick: "TradeTick") -> None:
        if self.sim is None:
            self.features.on_trade(tick.event_time, tick.price, tick.qty, tick.is_buyer_maker)
            self.recorded_trades.append((tick.event_time, tick.price, tick.qty, tick.is_buyer_maker))
        else:
            self.sim.on_trade(tick.event_time, tick.price, tick.qty, tick.is_buyer_maker)
            self._collect_fills()

    def _collect_fills(self) -> None:
        for acc in self.accounts:
            seen = self._fill_counts.get(acc.name, 0)
            for f in acc.fills[seen:]:
                self.recent_fills.append((acc.name, f))
            self._fill_counts[acc.name] = len(acc.fills)

    # ── calibration ─────────────────────────────────────────────────────────
    def _warmup_data(self) -> MarketData:
        b = np.array(self.recorded_book, dtype=float)
        t = np.array(self.recorded_trades, dtype=float) if self.recorded_trades else np.zeros((0, 4))
        return MarketData(
            name="live warm-up", venue=self.venue, tick_size=self.config.tick_size,
            book_ts=b[:, 0].astype(np.int64), bid=b[:, 1], bid_qty=b[:, 2], ask=b[:, 3],
            ask_qty=b[:, 4], trade_ts=t[:, 0].astype(np.int64), trade_px=t[:, 1],
            trade_qty=t[:, 2], trade_ibm=t[:, 3].astype(bool))

    @property
    def warmup_elapsed_s(self) -> float:
        if self.first_event_ms is None or not self._mid_ts:
            return 0.0
        return (self._mid_ts[-1] - self.first_event_ms) / 1000.0

    async def _calibrate(self) -> None:
        self._calibrating = True
        self.status = "calibrating"
        try:
            data = self._warmup_data()
            calib = await asyncio.to_thread(calibrate, data)
            self.features.set_hawkes(HawkesParams.from_dict(calib["hawkes"]["buy"]),
                                     HawkesParams.from_dict(calib["hawkes"]["sell"]))
            self.accounts = [SimAccount(s, self.config) for s in self.build_suite(calib)]
            self.sim = MarketSimulator(self.features, self.accounts, self.decision_ms)
            self.calibration = calib
            self.recorded_book.clear()
            self.recorded_trades.clear()
            self.status = "trading"
            self._next_refit = time.monotonic() + self.refit_s
            logger.info("live calibration: sigma=%.4f A=%.3f k=%.3f", calib["sigma"],
                        calib["arrival"]["A"], calib["arrival"]["k"])
        except Exception as exc:  # noqa: BLE001 - surfaced in the dashboard, retried later
            self.error = f"calibration failed: {exc}"
            self.status = "warming up"
            self.warmup_s += 60.0
            logger.exception("live calibration failed")
        finally:
            self._calibrating = False

    async def _refit(self) -> None:
        params = await asyncio.to_thread(FeatureEngine.fit_hawkes, self.features.recent_events())
        if params is not None:
            self.features.set_hawkes(*params, calibrated=True)

    async def maintain(self) -> None:
        """Call periodically from the UI loop: calibrates and refits when due."""
        if self.sim is None and not self._calibrating and self.warmup_elapsed_s >= self.warmup_s:
            await self._calibrate()
        elif self.sim is not None and time.monotonic() >= self._next_refit:
            self._next_refit = time.monotonic() + self.refit_s
            asyncio.create_task(self._refit())

    # ── reporting ───────────────────────────────────────────────────────────
    def reports(self) -> list[dict]:
        if self.sim is None or not self.sim.sample_times:
            return []
        mid_ts = np.asarray(self._mid_ts, dtype=np.int64)
        mids = np.asarray(self._mids, dtype=float)
        return [account_report(acc, self.sim.sample_times, mid_ts, mids,
                               fee_aware=bool(getattr(acc.strategy, "fee_aware", False)))
                for acc in self.accounts]

    def mark_to_market(self) -> list[dict]:
        """Cheap per-frame view: equity, position, fills per account."""
        f = self.features
        mid = 0.5 * (f.bid + f.ask) if f.ready else 0.0
        out = []
        for a in self.accounts:
            maker = [x for x in a.fills if x[6]]
            out.append({"name": a.name, "equity": a.equity_at(mid) if mid else 0.0,
                        "position": a.position, "fills": len(maker),
                        "filled_qty": sum(x[3] for x in maker), "sent_qty": a.sent_qty,
                        "fees": a.fees, "orders": a.orders_sent, "halted": a.halted,
                        "equity_curve": a.equity,
                        "bid": a._working[1], "ask": a._working[-1]})
        return out
