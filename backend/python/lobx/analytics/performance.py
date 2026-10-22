"""Risk-adjusted performance, PnL attribution and bootstrap confidence intervals.

PnL attribution for a passive fill (s = +1 buy, -1 sell, Q = quantity):
    edge        = s * (mid_at_fill - price) * Q          spread captured vs mid
    markout(h)  = s * (mid_{t+h} - price) * Q            value after h ms
    adverse(h)  = edge - markout(h) = -s * (mid_{t+h} - mid_t) * Q
so  edge - adverse(h) - fees  is what a fill is worth if closed at the mid
after h ms. ``inventory_pnl`` is the residual from holding positions longer.

Sharpe is computed on 1-minute PnL increments and annualised with
sqrt(525,600) (crypto trades every minute of the year). Over a few hours this
is a noisy statistic, which is why every comparison also gets a block
bootstrap interval.
"""
from __future__ import annotations

import math

import numpy as np

from lobx.execution.simulator import BID, SimAccount

MINUTES_PER_YEAR = 525_600.0


def minute_pnl(sample_times: list[int], equity: list[float]) -> np.ndarray:
    """PnL per calendar minute from equity samples (previous-tick at boundaries)."""
    if len(equity) < 2:
        return np.zeros(0)
    t = np.asarray(sample_times[: len(equity)], dtype=np.int64)
    e = np.asarray(equity, dtype=float)
    grid = np.arange(t[0] - t[0] % 60_000 + 60_000, t[-1] + 1, 60_000)
    if len(grid) == 0:
        return np.array([e[-1] - e[0]])
    idx = np.searchsorted(t, grid, side="right") - 1
    levels = np.concatenate([[e[0]], e[idx], [e[-1]]])
    return np.diff(levels)


def sharpe(pnl: np.ndarray) -> float:
    if len(pnl) < 3:
        return 0.0
    sd = pnl.std(ddof=1)
    if sd <= 1e-12:
        return 0.0
    return float(pnl.mean() / sd * math.sqrt(MINUTES_PER_YEAR))


def max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    e = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(np.concatenate([[0.0], e]))[1:]
    return float(np.max(peak - e))


def account_report(acc: SimAccount, sample_times: list[int], mid_ts: np.ndarray,
                   mids: np.ndarray, horizons_ms: tuple[int, ...] = (1_000, 5_000, 30_000),
                   fee_aware: bool = False) -> dict:
    fills = acc.fills
    maker = [f for f in fills if f[6]]
    taker = [f for f in fills if not f[6]]
    report: dict = {
        "strategy": acc.name,
        "fills": len(maker),
        "taker_fills": len(taker),
        "orders_sent": acc.orders_sent,
        "cancels_sent": acc.cancels_sent,
        "rejects": acc.rejects,
        "maker_qty": float(sum(f[3] for f in maker)),
        "fill_ratio": float(sum(f[3] for f in maker)) / acc.sent_qty if acc.sent_qty else 0.0,
        "turnover": float(sum(f[2] * f[3] for f in fills)),
        "fees": acc.fees,
        "maker_fees": float(sum(f[4] for f in maker)),
        "taker_fees": float(sum(f[4] for f in taker)),
        "final_position": acc.position,
        "halted": acc.halted,
    }
    net = acc.equity[-1] if acc.equity else 0.0
    report["net_pnl"] = net
    report["gross_pnl"] = net + acc.fees

    if maker:
        arr = np.array([(f[0], 1.0 if f[1] == BID else -1.0, f[2], f[3], f[5]) for f in maker])
        ts, s, px, q, mid0 = arr.T
        edge = s * (mid0 - px) * q
        report["edge"] = float(edge.sum())
        report["edge_per_fill_usd"] = float(edge.mean())
        for h in horizons_ms:
            idx = np.searchsorted(mid_ts, ts + h, side="right") - 1
            idx = np.clip(idx, 0, len(mids) - 1)
            mo = s * (mids[idx] - px) * q
            report[f"markout_{h}ms"] = float(mo.sum())
            report[f"adverse_{h}ms"] = float((edge - mo).sum())
        h_ref = 5_000 if 5_000 in horizons_ms else horizons_ms[0]
        report["adverse_selection_cost"] = report[f"adverse_{h_ref}ms"]
        report["adverse_bps"] = report["adverse_selection_cost"] / max(
            float((px * q).sum()), 1e-12) * 1e4
        report["inventory_pnl"] = report["gross_pnl"] - report["edge"]
    else:
        report.update({"edge": 0.0, "edge_per_fill_usd": 0.0, "adverse_selection_cost": 0.0,
                       "adverse_bps": 0.0, "inventory_pnl": report["gross_pnl"]})
        for h in horizons_ms:
            report[f"markout_{h}ms"] = 0.0
            report[f"adverse_{h}ms"] = 0.0

    pnl = minute_pnl(sample_times, acc.equity)
    hours = (sample_times[-1] - sample_times[0]) / 3_600_000 if len(sample_times) > 1 else 0.0
    report["sharpe"] = sharpe(pnl)
    report["max_drawdown"] = max_drawdown(acc.equity)
    report["pnl_per_hour"] = net / hours if hours > 0 else 0.0
    report["fills_per_hour"] = len(maker) / hours if hours > 0 else 0.0
    report["max_abs_position"] = float(max((abs(p) for p in acc.positions), default=0.0))
    report["mean_abs_position"] = float(np.mean(np.abs(acc.positions))) if acc.positions else 0.0
    maker_turnover = float(sum(f[2] * f[3] for f in maker))
    report["maker_turnover"] = maker_turnover
    # Fee level at which net PnL is zero; fills do not depend on the fee unless
    # the strategy is fee-aware, so it is only reported for fee-blind strategies.
    if maker_turnover > 0 and not fee_aware:
        report["breakeven_maker_fee_bps"] = (report["gross_pnl"] - report["taker_fees"]) \
            / maker_turnover * 1e4
    else:
        report["breakeven_maker_fee_bps"] = None
    report["_minute_pnl"] = pnl
    return report


def block_bootstrap(series: dict[str, np.ndarray], baseline: str | None = None,
                    block: int = 10, n_boot: int = 2000, seed: int = 7,
                    level: float = 0.95) -> dict[str, dict]:
    """Moving-block bootstrap of per-minute PnL, paired across strategies.

    The same resampled blocks are used for every strategy, so differences
    against ``baseline`` keep the cross-strategy correlation of a shared market.
    """
    names = list(series)
    n = min(len(series[k]) for k in names)
    if n < 3:
        return {}
    data = np.vstack([np.asarray(series[k][:n], dtype=float) for k in names])
    rng = np.random.default_rng(seed)
    block = max(1, min(block, n))
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(n_boot, -1)[:, :n]
    totals = data[:, idx].sum(axis=2)  # (strategies, n_boot)
    lo, hi = (1 - level) / 2 * 100, (1 + level) / 2 * 100
    out: dict[str, dict] = {}
    for i, name in enumerate(names):
        sample = data[i][idx]
        sd = sample.std(axis=1, ddof=1)
        sh = np.where(sd > 1e-12, sample.mean(axis=1) / np.where(sd > 1e-12, sd, 1.0), 0.0) \
            * math.sqrt(MINUTES_PER_YEAR)
        entry = {
            "pnl": float(data[i].sum()),
            "pnl_ci": [float(np.percentile(totals[i], lo)), float(np.percentile(totals[i], hi))],
            "sharpe_ci": [float(np.percentile(sh, lo)), float(np.percentile(sh, hi))],
            "p_positive": float(np.mean(totals[i] > 0)),
        }
        if baseline is not None and baseline in names and name != baseline:
            j = names.index(baseline)
            diff = totals[i] - totals[j]
            entry["diff_vs_baseline"] = float(data[i].sum() - data[j].sum())
            entry["diff_ci"] = [float(np.percentile(diff, lo)), float(np.percentile(diff, hi))]
            entry["p_better_than_baseline"] = float(np.mean(diff > 0))
        out[name] = entry
    return out
