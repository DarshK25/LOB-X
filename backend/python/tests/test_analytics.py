"""Performance metrics, PnL attribution, bootstrap, calibration and the backtest engine."""
import numpy as np
import pytest

from lobx.analytics.calibration import calibrate, fit_arrival, select_fair_value
from lobx.analytics.performance import (
    account_report,
    block_bootstrap,
    deflated_sharpe,
    inventory_episode_report,
    max_drawdown,
    minute_pnl,
    probabilistic_sharpe,
    sharpe,
)
from lobx.backtest.data import MarketData
from lobx.backtest.engine import run_backtest
from lobx.backtest.research import walk_forward_slices
from lobx.execution.simulator import BID, ExecutionConfig, SimAccount
from lobx.strategy import FixedSpreadStrategy, NoTradeStrategy
from lobx.strategy.features import FeatureEngine


def synthetic_market(seconds=1800, seed=5, tick=0.1, sigma=1.0):
    """Random-walk mid on a tick grid, 1-tick spread, Poisson market orders."""
    rng = np.random.default_rng(seed)
    n = seconds * 10
    ts = np.arange(n, dtype=np.int64) * 100 + 1_700_000_000_000
    mid_ticks = np.cumsum(rng.choice([-1, 0, 1], size=n, p=[0.1, 0.8, 0.1]))
    mid = 70_000.05 + mid_ticks * tick * sigma
    bid = np.round(mid - tick / 2, 1)
    ask = bid + tick
    bq = rng.uniform(0.5, 5.0, n)
    aq = rng.uniform(0.5, 5.0, n)
    m = int(seconds * 2)
    tts = np.sort(rng.choice(ts, size=m, replace=False)) + 1
    idx = np.searchsorted(ts, tts, side="right") - 1
    ibm = rng.random(m) < 0.5
    depth_ticks = rng.geometric(0.6, size=m) - 1
    px = np.where(ibm, bid[idx] - depth_ticks * tick, ask[idx] + depth_ticks * tick)
    qty = rng.uniform(0.001, 0.5, m)
    return MarketData("synthetic", "test", tick, ts, bid, bq, ask, aq, tts, px, qty, ibm)


def test_sharpe_and_drawdown():
    assert sharpe(np.array([1.0, 1.0, 1.0])) == 0.0
    pnl = np.array([1.0, -0.5, 1.0, -0.5])
    assert sharpe(pnl) > 0
    assert max_drawdown([0.0, 2.0, 1.0, 3.0, 0.5]) == pytest.approx(2.5)


def test_minute_pnl_sums_to_total():
    t = list(range(0, 600_000, 1000))
    eq = list(np.cumsum(np.ones(len(t))))
    pnl = minute_pnl(t, eq)
    assert pnl.sum() == pytest.approx(eq[-1] - eq[0])


def test_bootstrap_is_paired_and_bounded():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 200)
    better = base + 0.5
    out = block_bootstrap({"base": base, "better": better}, baseline="base", n_boot=500)
    assert out["better"]["p_better_than_baseline"] == 1.0  # identical noise, constant edge
    lo, hi = out["base"]["pnl_ci"]
    assert lo < base.sum() < hi


def test_attribution_edge_minus_adverse_equals_markout():
    acc = SimAccount(FixedSpreadStrategy(0.05, 1.0), ExecutionConfig(tick_size=0.1, maker_fee=0.0))
    acc._mid = 100.0
    acc._apply_fill(1_000, BID, 99.95, 1.0, True)
    acc.equity = [0.0, -0.95]
    mid_ts = np.array([0, 1_000, 2_000, 6_000, 31_000])
    mids = np.array([100.0, 100.0, 99.5, 99.0, 99.0])
    rep = account_report(acc, [0, 31_000], mid_ts, mids)
    assert rep["edge"] == pytest.approx(0.05)
    assert rep["markout_5000ms"] == pytest.approx(99.0 - 99.95)
    assert rep["adverse_5000ms"] == pytest.approx(rep["edge"] - rep["markout_5000ms"])
    assert rep["fill_win_rate_5000ms"] == 0.0
    assert rep["fill_expectancy_5000ms"] == pytest.approx(99.0 - 99.95)


def test_fill_win_rate_is_net_of_actual_maker_fee():
    cfg = ExecutionConfig(tick_size=0.1, maker_fee=0.001)
    acc = SimAccount(FixedSpreadStrategy(0.05, 1.0), cfg)
    acc._mid = 100.0
    acc._apply_fill(1_000, BID, 99.95, 1.0, True)
    acc.equity = [0.0, 0.0]
    acc.positions = [0.0, 1.0]
    rep = account_report(acc, [0, 6_000], np.array([0, 6_000]), np.array([100.0, 100.0]))
    assert rep["markout_5000ms"] == pytest.approx(0.05)
    assert rep["maker_fees"] == pytest.approx(0.09995)
    assert rep["net_markout_5000ms"] == pytest.approx(-0.04995)
    assert rep["fill_win_rate_5000ms"] == 0.0


def test_inventory_episode_metrics_use_completed_flat_to_flat_cycles():
    out = inventory_episode_report(
        [0, 1_000, 2_000, 3_000, 4_000, 5_000],
        [0.0, 0.0, 2.0, 3.0, 3.0, 2.0],
        [0.0, 1.0, 1.0, 0.0, -1.0, 0.0],
    )
    assert out["inventory_episodes"] == 2
    assert out["closed_inventory_episodes"] == 2
    assert out["inventory_episode_win_rate"] == 0.5
    assert out["inventory_episode_expectancy"] == pytest.approx(1.0)
    assert out["mean_time_to_flat_s"] == pytest.approx(1.5)


def test_probabilistic_and_deflated_sharpe_penalise_multiple_trials():
    rng = np.random.default_rng(9)
    pnl = rng.normal(0.1, 1.0, 500)
    psr = probabilistic_sharpe(pnl)
    assert 0.5 < psr < 1.0
    assert deflated_sharpe(pnl, 100) < psr


def test_arrival_fit_recovers_decay_on_synthetic_market():
    data = synthetic_market()
    fit = fit_arrival(data, min_count=5)
    assert fit["k"] > 0 and fit["A"] > 0
    assert fit["r2"] > 0.8


def test_calibrate_and_backtest_end_to_end_on_synthetic_market():
    data = synthetic_market(seconds=1200)
    calib = calibrate(data)
    assert calib["sigma"] > 0
    feats = FeatureEngine(data.tick_size, sigma_seed=calib["sigma"])
    cfg = ExecutionConfig(tick_size=data.tick_size, maker_fee=0.0, taker_fee=0.0)
    accounts = [SimAccount(NoTradeStrategy(0.001), cfg),
                SimAccount(FixedSpreadStrategy(0.05, 0.001), cfg)]
    run = run_backtest(data, accounts, feats)
    rows = run.by_name()
    assert rows["NoTradeStrategy"]["net_pnl"] == 0.0
    fixed = rows["FixedSpreadStrategy"]
    assert fixed["fills"] > 0
    assert fixed["net_pnl"] == pytest.approx(fixed["gross_pnl"])  # zero fees
    assert run.book_events == len(data.book_ts)


def test_fair_value_selection_uses_later_fold_and_gates_negative_r2(monkeypatch):
    data = synthetic_market(seconds=10)
    calib = {"hawkes": {}, "sigma": 1.0}
    monkeypatch.setattr(
        "lobx.analytics.calibration.fit_fair_value",
        lambda *_args: {"horizon_ms": _args[-1], "b_micro": 0.0,
                                    "b_flow": 1.0, "b_obi": 1.0, "b_volume": 1.0},
    )
    monkeypatch.setattr("lobx.analytics.calibration.fair_value_r2",
                        lambda _data, trial: -trial["fair_value"]["horizon_ms"] / 1_000)
    selected = select_fair_value(data, data, calib, (250, 500))
    assert selected["horizon_ms"] == 250
    assert selected["validation_r2"] == -0.25
    assert selected["enabled"] is False
    assert selected["b_flow"] == selected["b_obi"] == selected["b_volume"] == 0.0


def test_walk_forward_slices_are_strictly_past_only():
    data = synthetic_market(seconds=1_200)
    folds = walk_forward_slices(data, train_s=600, test_s=300, step_s=300)
    assert len(folds) == 2
    for train, test in folds:
        assert train.end < test.start
        assert train.hours == pytest.approx(600 / 3600, abs=1e-3)
        assert test.hours == pytest.approx(300 / 3600, abs=1e-3)
    assert folds[0][1].end < folds[1][1].start
