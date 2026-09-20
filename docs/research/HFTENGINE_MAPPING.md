# HFTENGINE → LOB-X Architecture Mapping

**Reference:** `_reference/hftengine/` (read-only, not imported)

| HFTENGINE Component | LOB-X Equivalent | Status |
|---------------------|------------------|--------|
| **Queue Model** | | |
| `ProbQueueModel` | `shadow_engine.queue_ahead` (linear) | Baseline exists |
| `PowerProbQueueFunc3(n=3)` | Not implemented | Design phase |
| Queue position tracking | `ShadowOrder.queue_ahead_qty` | Static L2 |
| **Latency Model** | | |
| Feed latency (measured) | Not implemented | Design phase |
| Order latency (derived) | `ExecutionAssumptions.latency_ms` (fixed) | Exists |
| Response latency | Not implemented | Design phase |
| **Fill Model** | | |
| Probabilistic fill | `shadow_engine._fill()` (binary crossing) | Baseline exists |
| Partial fills | Not implemented | Future |
| GTX enforcement | Not implemented | Design phase |
| **Exchange Model** | | |
| `NoPartialFillExchange` | `ShadowQuoteEngine` | Conceptually similar |
| Order types | Limit only | Simplified |
| **Replay** | | |
| Event recorder | `backtest/replay.py` | Equivalent |
| Data loader | `HistoricalReplay.from_parquet()` | Equivalent |
| State snapshots | `MarketState` | Equivalent |
| **Data** | | |
| L2 depth | Binance futures L2 | Same source |
| Trades | Binance aggTrades | Same source |
| **Calibration** | | |
| Queue model `n` | Not calibrated | Future |
| Latency distribution | Fixed assumption | Future |

## Key Differences

1. **Language:** HFTENGINE (Rust) vs LOB-X (Python/C++)
2. **Queue:** HFTENGINE probabilistic vs LOB-X linear consumption
3. **Latency:** HFTENGINE measured+derived vs LOB-X fixed constant
4. **Fills:** HFTENGINE probabilistic vs LOB-X binary crossing
5. **Purpose:** HFTENGINE live trading vs LOB-X research/backtest

## Implementation Plan

See `docs/research/EXECUTION_REALISM_DESIGN.md` for detailed design.

**Phase A:** Probabilistic queue model (4 weeks)  
**Phase B:** Latency models (2 weeks)  
**Phase C:** Integration & testing (2 weeks)  
**Phase D:** Calibration (2 weeks)

**Status:** Design complete, implementation NOT started.
