"""Single-pass multi-account backtest over a ``MarketData`` window."""
from __future__ import annotations

import time
from dataclasses import dataclass

from lobx.analytics.performance import account_report
from lobx.backtest.data import MarketData
from lobx.execution.simulator import MarketSimulator, SimAccount
from lobx.strategy.features import FeatureEngine


@dataclass
class BacktestRun:
    data_name: str
    hours: float
    book_events: int
    trade_events: int
    seconds: float
    reports: list[dict]
    sample_times: list[int]
    accounts: list[SimAccount]

    def by_name(self) -> dict[str, dict]:
        return {r["strategy"]: r for r in self.reports}


def run_backtest(data: MarketData, accounts: list[SimAccount], features: FeatureEngine,
                 decision_ms: int = 100, flatten: bool = True) -> BacktestRun:
    sim = MarketSimulator(features, accounts, decision_interval_ms=decision_ms)
    on_book, on_trade = sim.on_book, sim.on_trade
    bts, bb, bq, ba, baq = (data.book_ts.tolist(), data.bid.tolist(), data.bid_qty.tolist(),
                            data.ask.tolist(), data.ask_qty.tolist())
    tts, tp, tq, ti = (data.trade_ts.tolist(), data.trade_px.tolist(), data.trade_qty.tolist(),
                       data.trade_ibm.tolist())
    nb, nt = len(bts), len(tts)
    i = j = 0
    t0 = time.perf_counter()
    while i < nb or j < nt:
        if j < nt and (i >= nb or tts[j] <= bts[i]):
            on_trade(tts[j], tp[j], tq[j], ti[j])
            j += 1
        else:
            on_book(bts[i], bb[i], bq[i], ba[i], baq[i])
            i += 1
    sim.finish(flatten=flatten)
    elapsed = time.perf_counter() - t0
    reports = [
        account_report(acc, sim.sample_times, data.book_ts, data.mids,
                       fee_aware=bool(getattr(acc.strategy, "fee_aware", False)))
        for acc in accounts
    ]
    return BacktestRun(data.name, data.hours, sim.book_events, sim.trade_events, elapsed,
                       reports, sim.sample_times, accounts)
