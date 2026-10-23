# LOB-X — Executive Summary

**Project:** Limit Order Book Exchange (LOB-X)  
**Review date:** 11 October 2026  
**Scope:** Current local working tree; source-code review and limited execution checks.

## 1. Overall assessment

LOB-X is a market-microstructure research and simulation platform with a C++ limit-order-book engine and a Python research stack. Its most developed workflow combines Binance market-data collection, historical replay, strategy-generated quotes, simulated fills, inventory accounting, and backtest reports.

The repository contains substantial implementation, but several integration and correctness issues prevent the complete application from working as described in the main project README. The available visualization is a Rich terminal dashboard. The React frontend contains empty directory scaffolding, with no application source or build configuration.

**Bottom line:** LOB-X has a useful research foundation. A complete browser dashboard, unified API state, reliable C++ market-data mirroring, and validated strategy calibration remain unfinished. The reviewed code performs simulated execution; it does not submit real exchange orders.

## 2. Architecture and execution paths

LOB-X contains three distinct workflows:

| Workflow | Data flow | Purpose |
| --- | --- | --- |
| Live collection and paper trading | Binance → Collector → Python L2 book → strategy → shadow execution → inventory/P&L | Observe real market data and simulate strategy execution |
| Historical backtesting | Captured depth and trade Parquet → chronological replay → strategy → shadow execution → reports | Evaluate strategies under explicit execution assumptions |
| Synthetic exchange simulation | Simulated traders → shared C++ order book → matching → trade log | Exercise an exchange model with artificial order flow |

The FastAPI REST/WebSocket application is a separate, partially connected interface to an in-memory C++ order book. It does not automatically share the Collector, simulation, or backtest state.

## 3. Module status

In this summary, **implemented** means executable logic exists in the reviewed source. It does not mean the module passed a fresh end-to-end test during this review.

| Module | Implemented behavior | Assessment |
| --- | --- | --- |
| C++ order book | Price-time priority, FIFO queues, partial fills, multi-level matching, cancellation, book queries | Substantial implementation; critical matching-loop and FOK defects identified |
| MatchingEngine wrapper | Basic order validation, statistics, trade callbacks | Exposed through bindings, but bypassed by primary API and simulation paths |
| pybind11 bindings | Python access to orders, trades, order book, engine and enums | Implemented; available local binary targets Windows CPython 3.13 |
| Market-data pipeline | Binance WebSocket connection, reconnect handling, REST snapshot synchronization, trade parsing | Implemented; historical collection evidence exists |
| C++ engine mirror | Converts L2 price levels into synthetic C++ orders | Incomplete initialization: initial REST snapshot is not copied into C++ |
| Storage | Daily Parquet files and optional batched PostgreSQL trade writes | Implemented; separate PostgresStorage and RedisStorage interfaces remain stubs |
| Synthetic simulation | Discrete tick loop and several trader archetypes | Implemented; configuration and participant accounting need improvement |
| Historical replay | Timestamp-ordered depth/trade processing, quote cadence, fees, flattening, reports | Implemented with explicit fill-model assumptions |
| Shadow execution and inventory | Hypothetical fills, latency, partial fills, queue-ahead approximation, average-cost P&L | Implemented; does not reconstruct true exchange queue priority |
| Strategies and risk | Fixed spread, inventory skew, AS, no-trade baseline, position/order/drawdown limits | Implemented; AS calibration and volatility units need further work |
| Quant models and analytics | AS formulas, Hawkes models, spread, returns, volatility, OBI, OFI, liquidity, and an Almgren–Chriss-style unwind planner | Research utilities exist; the unwind component plans reduce-only slices but does not submit orders |
| FastAPI | Health, book snapshot, order submission, cancellation, WebSocket snapshots | REST and WebSocket use different order books |
| Terminal dashboard | L2 display, candles, trade feed, microstructure metrics, quote preview | Implemented in scripts/lobx_cli.py |
| React frontend | Empty charts/components/hooks/pages/services/store/types/utils folders | No runnable browser application |
| Deployment | Compose definition and scheduled collection workflow | Compose references missing Dockerfiles; deployment directories are scaffolding |

## 4. How the charts work

### Terminal candles and order-book display

The implemented chart is `TerminalCandleChart` in `scripts/lobx_cli.py`. Trade callbacks feed it prices and quantities. Trades are grouped into five-second buckets, and each bucket maintains open, high, low, close, and volume.

- Wicks use vertical line characters; candle bodies use block characters.
- Green means close is at or above open; red means close is below open.
- The dashboard retains up to 60 candles and rescales their prices into an eight-row display.
- The display refreshes approximately four times per second.
- The L2 panel shows the best five price levels per side and quantity bars scaled to the largest displayed quantity.

**Important interpretation:** candle buckets use local processing time, not the exchange timestamp. Empty time buckets are not inserted. Volume is accumulated but not plotted as a separate series. The trade-feed BUY/SELL labels describe the external market aggressor, not fills made by a LOB-X strategy.

### Quantitative dashboard panel

The panel calculates order-book imbalance and microprice from the current Python reference book. Its AS quote preview uses hardcoded parameters and zero inventory. Its Hawkes gauge uses hardcoded parameters and wall-clock trade arrival times. These previews are not connected to the live paper strategy's inventory or an accepted calibration.

### Browser and WebSocket status

There is no implemented React chart or browser P&L dashboard. `/ws/book` sends top-of-book prices and aggregate quantities every 100 ms. It does not provide full L2 levels, candle history, strategy equity, or fill events. It also reads a different book from the REST order routes.

## 5. How trade simulation works

### A. Synthetic C++ exchange simulation

`scripts/run_simulation.py` registers five random traders, one momentum trader, one mean-reversion trader, one naive market maker, and one simulation-oriented AS market maker.

For every tick, traders act sequentially on a shared C++ book. Incoming limit orders cross eligible opposing prices, fill FIFO at each level, and produce trade records. GTC remainders rest in the book. The simulator aggregates trade count, volume, and the trade log.

The current script does not load `configs/simulation.yaml`, uses the same random seed for all five noise traders, and does not produce comprehensive per-participant inventory and P&L. The separate institutional trader and Almgren–Chriss executor are not registered by this default script.

### B. Historical replay and live paper execution

The historical path loads recorded depth and trades, reconstructs a Python L2 book, and processes events in timestamp order. A strategy receives a `MarketState` containing prices, quantities, spread, volatility, inventory, and event time. It returns a bid/ask `QuotePair`.

The risk manager suppresses disallowed quotes. The shadow broker then tracks at most one bid and one ask:

- A seller-aggressor trade at or below the shadow bid can fill that bid.
- A buyer-aggressor trade at or above the shadow ask can fill that ask.
- Fills are recorded at the strategy's quote price.
- Quantity is limited by the quote remainder and eligible external trade quantity.
- Quotes become eligible only after their configured activation latency.
- The optional queue-ahead model consumes estimated displayed quantity before awarding fills.

The live `StrategyRunner` uses the same strategy/shadow-execution pattern with incoming Binance data. The historical replay uses the Python book directly; it does not execute strategy fills through the C++ matcher.

### Execution assumptions and accounting

| Scenario in run_backtest.py | Activation latency | Eligible trade quantity | Queue model |
| --- | --- | --- | --- |
| Optimistic | 0 ms | 100% | Immediate shadow eligibility |
| Baseline | 100 ms | 100% | Shadow eligibility |
| Conservative | 250 ms | 50% | Displayed queue-ahead approximation |

The CLI defaults assume a 0.02% maker fee and a 0.05% taker fee for forced flattening. These are scenario inputs, not verified venue fees. Inventory is tracked using average cost; net P&L is realized plus unrealized P&L minus fees.

The historical backtest closes remaining inventory at the final best bid/ask when flattening is enabled. Outputs include JSON summaries, fill CSVs, equity CSVs, and a data-hash/code-revision manifest. The live strategy prints a session summary and does not apply the historical runner's end-of-session flattening routine.

## 6. Strategy and model interpretation

- **Fixed spread:** quotes symmetrically around mid, ignoring inventory and volatility.
- **Inventory skew:** shifts both quotes away from accumulated inventory to encourage position reduction.
- **Avellaneda–Stoikov:** computes an inventory-adjusted reservation price and a model-based spread. Default gamma and kappa are scenario placeholders.
- **No-trade:** never quotes and provides a zero-P&L accounting benchmark.
- **Almgren–Chriss-style unwind:** supplies a reduce-only inventory trajectory/planner; it is not an exchange-order executor.
- **Hawkes:** supplies event-intensity, simulation, and calibration utilities; the terminal gauge is a separate heuristic implementation.

The AS pricing equations are present, but the live/replay volatility estimator returns per-update log-return volatility while remaining horizon is measured in seconds. A consistent price-volatility and time-unit convention is needed before interpreting inventory skew or spread output as calibrated behavior.

Default paper risk limits are 0.005 BTC absolute position, 0.001 BTC order size, and 25 USDT drawdown. A drawdown halt cancels quoting when applied; it does not immediately liquidate the existing position or guarantee that subsequent losses remain below the threshold.

## 7. Existing research results

The saved 2026-08-14 reports record the following outcomes. These are existing artifacts, not backtests rerun during this review.

| Strategy/scenario | Net P&L, USDT | Fees, USDT | Maximum drawdown, USDT | Risk halted |
| --- | --- | --- | --- | --- |
| Fixed spread / baseline | -23.38 | 15.66 | 25.43 | Yes |
| AS / baseline | -26.36 | 15.64 | 26.98 | Yes |
| AS / conservative | -23.22 | 16.05 | 25.25 | Yes |

These reports do not establish profitability or demonstrate consistent AS superiority. The recorded one-second markouts are negative, and fees materially reduce performance. The exact 2026-08-14 source capture referenced by the reports was not available in the reviewed data directory for reproduction.

The current calibration artifacts explicitly invalidate earlier placeholder outputs. Project documentation records rejected kappa fits with R² values of 0.355 and 0.119, below the 0.50 acceptance threshold. No accepted data-derived kappa is established by those artifacts, and gamma remains an assumption or heuristic rather than a directly fitted parameter.

## 8. Highest-priority findings

1. **C++ reference lifetime defect:** the matching loop accesses a passive-order reference after `reduce_front()` can remove that order from its deque. Copy the required ID/state before removal.
2. **Incorrect FOK behavior:** matching occurs before full executability is established, so an FOK order can partially trade. Add a full-liquidity precheck or an atomic execution mechanism.
3. **Split API book state:** REST routes use their own `_book`, while `/ws/book` uses `main.book`. Use one shared dependency and verify that submitted orders appear in streamed state.
4. **Incomplete C++ mirror initialization:** the initial REST-derived Python book is not seeded into the C++ mirror. Rebuild the mirror from the authoritative book on initial sync and resync.
5. **Simulation validity gaps:** configuration is hardcoded, random agents share a seed, and participant accounting is incomplete. Verify unique order IDs, passive-fill attribution, and independent agent behavior.
6. **Model integration gaps:** volatility units, calibrated parameter loading, and the connection between displayed quote previews and actual paper execution need alignment.
7. **Missing application/deployment pieces:** the React frontend and referenced Dockerfiles are absent. The report generator also needs interface alignment with `run_analysis()` before report regeneration is dependable.

Key source locations: `backend/cpp/src/order_book.cpp`, `backend/cpp/include/lobx/price_level.hpp`, `backend/python/main.py`, `backend/python/lobx/api/routes.py`, `backend/python/lobx/market_data/collector.py`, and `scripts/run_simulation.py`.

## 9. Verification and limits of this review

- Python static compilation passed for `backend/python`, `scripts`, and `benchmarks`.
- The Python test suite could not run in the review environment because `pytest` was not installed; a Collector-related import also failed on missing `websockets`.
- CMake was unavailable. The local C++ extension targets Windows CPython 3.13, whereas the review shell uses Linux and Python 3.12.3.
- No fresh live-feed, database, API/WebSocket, C++ runtime, or full backtest verification was completed.
- `docs/gap_closure_report.md` records historical evidence including 45 passing Python tests and a 14.5-hour collection session. Those results are historical, not a current verification claim.
- The initial review observed pre-existing local modifications across the working tree. Findings describe the reviewed source, not a clean release or a deployment certification.

## 10. Recommended next milestones

### Milestone 1 — Correctness

Fix the C++ lifetime and FOK defects, unify API state, and initialize/reinitialize the engine mirror correctly. Add targeted regression coverage for each failure mode.

### Milestone 2 — Reproducible research

Resolve volatility and horizon units, improve participant accounting, use independent random streams, load simulation configuration, and make calibration/report interfaces consistent. Retain data hashes, assumptions, strategy parameters, fills, and equity artifacts for every comparison.

### Milestone 3 — Integrated visualization

Implement the frontend application and a complete event contract for L2 depth, exchange-time candles, strategy quotes, fills, inventory, and net equity. Clearly identify live market observations, model previews, and simulated executions in the interface.

### Milestone 4 — Repeatable setup and verification

Provide the missing deployment files, portable build instructions, and integration checks covering the Collector, mirror, API/WebSocket, and replay. Repeat strategy comparisons across independent captures and execution scenarios.

## 11. Source map

| Area | Primary location |
| --- | --- |
| C++ matching and bindings | `backend/cpp/` |
| Binance collection and book reconstruction | `backend/python/lobx/market_data/` |
| Parquet and PostgreSQL writers | `backend/python/lobx/storage/` |
| Synthetic participants and loop | `backend/python/lobx/simulation/` |
| Historical replay | `backend/python/lobx/backtest/replay.py` |
| Shadow execution and inventory | `backend/python/lobx/execution/` |
| Strategies and live runner | `backend/python/lobx/strategy/`, `backend/python/lobx/live/` |
| Risk and quantitative utilities | `backend/python/lobx/risk/`, `backend/python/lobx/models/`, `backend/python/lobx/analytics/` |
| API | `backend/python/main.py`, `backend/python/lobx/api/` |
| Terminal dashboard | `scripts/lobx_cli.py` |
| Backtest and calibration entry points | `scripts/run_backtest.py`, `scripts/analyze_market.py` |
| Saved outcomes and historical evidence | `reports/backtests/`, `reports/calibration.json`, `docs/gap_closure_report.md` |

**Final assessment:** prioritize correctness and reproducibility before extending the UI or interpreting strategy output as validated trading performance. The codebase already provides a substantial foundation for market-data engineering and paper-trading research.
