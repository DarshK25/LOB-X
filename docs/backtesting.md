# Historical Backtesting and Paper-Trading Gates

`LOB-X` uses the real depth and trade capture as the source of truth for
strategy validation. The replay is deterministic: identical Parquet files,
strategy parameters, assumptions, and code revision must yield identical
fills and P&L.

## Run a historical replay

Run from the repository root after installing the Python dependencies:

```powershell
python scripts/run_backtest.py --date 2026-08-14
```

This runs fixed spread and Avellaneda--Stoikov under three named execution
scenarios. The AS `kappa` value is explicitly a scenario input, not a
data-calibrated estimate. Override it only with a recorded hypothesis:

```powershell
python scripts/run_backtest.py --date 2026-08-14 --strategy as --scenario conservative --kappa 1.5
```

Outputs under `reports/backtests/` include a data-hash/code-revision manifest,
one JSON summary per run, all simulated fills, and an equity curve. Never
compare aggregate P&L without retaining those artefacts.

## Execution scenarios

`optimistic` uses an immediate shadow fill and is a diagnostic upper bound.
`baseline` introduces 100 ms quote activation latency. `conservative` uses
250 ms activation latency, 50% eligible trade quantity, and an L2
queue-ahead approximation. All scenarios deduct the configured maker fee and
use the configured taker fee when flattening remaining inventory at session
end. Defaults are assumptions, not venue fee claims; configure the actual
account tier before drawing conclusions.

The queue model is not a full queue reconstruction: public Binance depth is
L2 and does not expose individual orders or cancellations ahead of us. A
strategy passing the conservative scenario has earned deeper research; it has
not earned a production deployment.

## Promotion gates

1. **Data gate:** the capture must start with the persisted two-sided depth
   snapshot. The runner refuses diff-only files rather than manufacturing an
   order book from incomplete state.
2. **Historical gate:** compare AS, fixed-spread, and no-trade economics over
   multiple independent sessions and volatility/liquidity regimes. AS must
   retain positive net P&L and bounded drawdown in conservative assumptions.
3. **Paper gate:** use `scripts/run_live_strategy.py` for an extended
   paper-only session. Compare predicted fills, one-second markouts, position,
   fees, and latency with the historical distribution. Reconcile every
   session from saved data.
4. **Capital gate:** requires a separately approved operational runbook,
   exchange credentials/permissions review, kill-switch test, monitoring, and
   a small fixed capital allocation. No code here submits real orders.

Hard inventory and drawdown controls live outside the strategy code and are
applied by both replay and live paper loops. A breach cancels both quote sides
for the remainder of that session.
