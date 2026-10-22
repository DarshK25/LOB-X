# Audit log: what was wrong and what changed

LOB-X began as a demo whose README described a different repository. This
page records the audit of the `dev` branch (October 2026) and how each finding
was resolved. The reasons are kept because they are the useful part.

## Why the paper trader always lost

A 24-second run of the original `sim` command: 18 fills out of 279 market
trades, $0.0026 of spread captured, $0.065 of fees, net −$0.072. There were
four causes, in order of size.

| # | Cause | Resolution |
|---|---|---|
| P1 | **Fees vs spread.** The model's $0.64 half-spread earned $0.64 per BTC. The 2 bp maker fee cost $16.34 per BTC. Every fill lost before the price even moved. | It cannot be fixed by quoting wider: wider quotes fill only when the price runs through them. The research now prices fee tiers explicitly (`execution/fees.py`), reports the break-even maker fee for every strategy, and has a fee-aware GLFT variant. The `scan` command shows the spread-to-fee ratio per symbol. See `research.md`. |
| P2 | **σ in the wrong unit.** A per-update log return of ~3·10⁻⁶ made γσ²τ ≈ 0. The spread was stuck at (2/γ)ln(1+γ/κ) = $1.29, a constant from the AS paper's stock example. | σ is now USD/√s from 1 s sampling (EWMA live, realized in backtests). κ and A are fitted in 1/USD and 1/s. γ is tuned in-sample. See `models.md`. |
| P3 | **Quotes 64 ticks behind a one-tick touch**, at a distance set by the constants above, not by the market. | Quote depth now comes from the fitted fill curve. In calm BTCUSDT futures tuned GLFT quotes about $4 from the mid when flat and skews $6.35 per lot of inventory; touch-quoting (FixedSpread) runs alongside as a baseline. Which depth pays is measured, not assumed. |
| P4 | **No queue model on the fill path.** The probabilistic queue model was imported only by its unit test. | It is now the default fill model. The old optimistic rule runs as a stress test and is shown to overstate PnL. See `execution_model.md`. |
| P5 | **Hard-coded γ = 0.1, κ = 1.5** in every command, with no calibration file. | Calibration plus γ grid search on in-sample data. `quote` and `sim` use calibrated values. |
| P6 | **No flattening on halt** and leftover inventory at the end of the run. | The kill switch cancels and liquidates with a taker order. Runs end flat and pay the taker fee for it. |

## False or stale claims

| # | Claim | Resolution |
|---|---|---|
| C1 | React/TypeScript dashboard with live charts | No frontend existed. README no longer claims one; it is listed as future work. |
| C2 | Almgren–Chriss, five trader archetypes, `lobx/simulation` | None existed. Claims removed. The math notes keep AC as study material. |
| C3 | C++ engine is "the core" | It was not built and not on any path that was run. It now builds on Windows without Visual Studio (Zig), is tested, is benchmarked, and backs the API. The README states plainly that research and paper trading use the Python simulator. |
| C4 | Docker stack with postgres, redis, api, frontend | Dockerfiles, frontend and Redis did not exist. Compose now runs only the optional Postgres for the trade writer. |
| C5 | ~934 MB curated parquet in `data/parquet` | Not in the repo. Replaced by `fetch_history.py` with a checksummed manifest (45 MB, two regimes). |

## Defects

| # | Defect | Resolution |
|---|---|---|
| D1 | C++ FOK traded before checking that it could complete | `fillable_qty` pre-check, regression tests |
| D2 | C++ use-after-free: passive order read after `reduce_front` popped it | id and done flag copied first, regression test |
| D3 | REST, websocket and `get_order_book()` were three different books | one cached book behind `Depends`, plus a lock |
| D4 | The engine mirror inserted levels with `add_limit_order`, so mirrored bids traded with mirrored asks; the snapshot was never loaded | snapshot load; removals before inserts; crossed levels cleared; test asserts zero trades |
| D5 | Live dashboard crashed on Windows (cp1252 cannot print the UI glyphs) | stdout reconfigured to UTF-8 |
| D6 | AS helpers disagreed on units (seconds vs session fraction; half vs full spread) | one convention, written in each module docstring and in `models.md` |
| D7 | C++ cancel was a linear scan, p99 137 µs (found while benchmarking) | iterator index, p99 0.5 µs |
| D8 | `fill_ratio` counted orders, not quantity, and went above 100% with partial fills | filled qty / sent qty |

## Dead, duplicate and mock code removed

* **Analytics.** 16 analytics modules that were not on any path, several of
  which needed `lobx_cpp` just to compute a spread.
* **Backtest and risk.** The old replay, `shadow_engine`, `inventory`,
  `portfolio` and `risk`. `RiskLimits` was never constructed. Execution now
  lives in one simulator.
* **Terminal.** The second terminal app (`terminal/app`, `views`, `charts`,
  `bridge`, `telemetry`) and the sparkline that `sim` drew by itself.
* **Storage.** The `PostgresStorage` and `RedisStorage` stubs (they raised
  `NotImplementedError`). The working Postgres trade writer is kept as
  optional.
* **Scripts and docs.** About 20 scripts and docs that referred to modules
  that do not exist, plus the YAML configs nothing read.

When two implementations did the same job, the one on the tested and used path
was kept. Where performance decided it (C++ cancel), it was benchmarked first.

## Decision: Postgres and Redis

Not needed for research or paper trading. Every input is a Parquet file and
every output is JSON or Markdown in `results/`, which keeps runs reproducible
and the setup to one `pip install`. Redis had no use case. The Postgres trade
writer stays as an optional collector sink (`pip install lobx[postgres]`) for
someone who wants months of live tape in a database.
