# LOB-X Execution Realism Implementation Queue

**Status:** Design complete, NO implementation started  
**Reference:** `docs/research/EXECUTION_REALISM_DESIGN.md`

## Priority Order

### 1. Probabilistic Queue Model (4 weeks)
**Files:**
- NEW: `backend/python/lobx/execution/queue_model.py`
- MODIFY: `backend/python/lobx/execution/shadow_engine.py`
- NEW: `backend/python/tests/test_queue_model.py`

**Tasks:**
- Implement `ProbabilisticQueueModel` class
- Add `prob_fill = 1 - (front_qty / level_qty)^n`
- Integrate into `ShadowQuoteEngine.on_trade()`
- Support modes: shadow (instant), linear (current), probabilistic (new)
- Deterministic RNG with seed
- Tests for mathematical bounds, lifecycle, determinism

**Deliverable:** Selectable queue model via `BacktestConfig.execution.queue_model`

### 2. Latency Models (2 weeks)
**Files:**
- MODIFY: `backend/python/lobx/execution/shadow_engine.py`
- MODIFY: `backend/python/lobx/backtest/replay.py`
- NEW: `backend/python/tests/test_latency_models.py`

**Tasks:**
- Add `LatencyModel` enum: instant, fixed, derived
- Implement derived latency: `order_latency = feed_latency * multiplier`
- Track feed latency from depth/trade timestamps
- Gate order submission/cancellation by latency
- Tests for each mode

**Deliverable:** Selectable latency via `BacktestConfig.execution.latency_mode`

### 3. GTX Enforcement (1 week)
**Files:**
- MODIFY: `backend/python/lobx/execution/shadow_engine.py`

**Tasks:**
- Add `enforce_gtx` flag to `ExecutionAssumptions`
- Reject orders that would cross spread
- Log rejection events

**Deliverable:** Optional GTX via `BacktestConfig.execution.enforce_gtx`

### 4. Spot-Only Mode (1 week)
**Files:**
- MODIFY: `backend/python/lobx/backtest/replay.py`
- MODIFY: `backend/python/lobx/market_data/binance_ws_client.py`

**Tasks:**
- Add spot orderbook parsing
- Validate against Binance spot L2
- Update tests

**Deliverable:** Support spot markets alongside futures

### 5. Queue Position Estimation (2 weeks)
**Files:**
- MODIFY: `backend/python/lobx/execution/queue_model.py`

**Tasks:**
- Track order arrival time
- Estimate position based on timestamp vs level updates
- Document assumptions

**Deliverable:** Improved `queue_ahead_qty` estimation

### 6. Calibration (2 weeks)
**Files:**
- NEW: `scripts/calibrate_queue_model.py`
- NEW: `backend/python/lobx/analytics/fill_calibration.py`

**Tasks:**
- Compare backtest fills to live fills
- Search for `n` that minimizes difference
- Document calibration procedure

**Deliverable:** Calibrated `n` parameter for probabilistic queue

### 7. Deterministic Replay Enhancement (1 week)
**Files:**
- MODIFY: `backend/python/lobx/backtest/replay.py`

**Tasks:**
- Add experiment provenance tracking
- Enhanced logging for queue/latency events
- Audit trail for fill decisions

**Deliverable:** Full reproducibility with detailed event log

## Implementation Rules

1. **No breaking changes** - all modes backward compatible
2. **Determinism first** - controlled RNG, no global randomness
3. **Test coverage** - every mode has unit tests
4. **No premature optimization** - Python first, C++ later if needed
5. **Document assumptions** - every model choice documented
6. **Preserve experiments** - existing results reproducible

## Not in Queue

- Partial fills (future work)
- Market impact (future work)
- Hidden liquidity (future work)
- Live exchange orders (out of scope)
- Reinforcement learning (out of scope)
- Terminal dashboard (separate project)
- C++ queue model (premature optimization)

## Next Step

Review `EXECUTION_REALISM_DESIGN.md` → Team approval → Begin Priority 1
