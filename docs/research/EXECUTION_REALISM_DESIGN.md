# EXECUTION REALISM DESIGN

**Date**: 2026-09-17  
**Status**: DESIGN PHASE ONLY - NO IMPLEMENTATION  
**Purpose**: Define how LOB-X should evolve toward realistic execution simulation based on HFTENGINE reference

---

## 1. Current LOB-X Execution Architecture

### Canonical Pipeline (VERIFIED)

```
Binance WebSocket (depth@0ms, trade, bookTicker)
    ↓
collector → raw gzip files
    ↓
convert → Parquet (depth, trades)
    ↓
HistoricalReplay.run()
    ├── LocalOrderBook (L2 mirror)
    ├── depth events → book updates
    ├── trade events → ShadowQuoteEngine.on_trade()
    └── MarketState.from_book()
        ↓
Strategy.on_tick(state)
    ↓
QuotePair(bid, ask)
    ↓
Risk.apply_quotes()
    ↓
ShadowQuoteEngine.update_quotes(quotes, timestamp, bid_ahead, ask_ahead)
    ├── Cancel-replace logic
    ├── Creates ShadowOrder(s)
    └── queue_ahead_qty = book.bids.get(price) or book.asks.get(price)
        ↓
WAIT for trade event
        ↓
ShadowQuoteEngine.on_trade(TradeTick)
    ├── Check: trade crosses our resting quote?
    ├── Check: timestamp >= order.active_at? (latency gate)
    ├── Check: queue_model logic
    └── _fill() if conditions met
        ↓
Portfolio.on_fill(side, price, qty, fee)
    ├── InventoryTracker (position, realized PnL)
    └── PortfolioEngine (cash, fees, gross/net)
        ↓
Fill records + events → analytics
```

**Key Files**:
- `backend/python/lobx/backtest/replay.py` - Orchestrates everything
- `backend/python/lobx/execution/shadow_engine.py` - Fill simulation
- `backend/python/lobx/execution/inventory.py` - Position tracking
- `backend/python/lobx/execution/portfolio.py` - Accounting
- `backend/python/lobx/strategy/base.py` - MarketState, Strategy ABC
- `backend/python/lobx/market_data/depth_stream_manager.py` - L2 book mirror

---

## 2. Current Fill Model (EXACT)

### Implementation: `ShadowQuoteEngine._fill()`

**Decision Rule**:
```python
# From on_trade() line 329-353:
if (bid_order exists AND
    trade.is_buyer_maker == True AND  # Sell aggressor
    timestamp >= bid_order.active_at AND  # Latency elapsed
    trade.price <= bid_order.price):  # Price crosses
    _fill(bid_order, trade)

if (ask_order exists AND  
    trade.is_buyer_maker == False AND  # Buy aggressor
    timestamp >= ask_order.active_at AND  # Latency elapsed
    trade.price >= ask_order.price):  # Price crosses
    _fill(ask_order, trade)
```

**Fill Logic** (lines 356-462):
```python
def _fill(order, trade):
    # 1. Check if canceled
    if order.status == "CANCELED": return
    
    # 2. Apply fill_ratio
    available_qty = trade.qty * assumptions.fill_ratio
    
    # 3. Queue model logic
    if assumptions.queue_model == "queue_ahead":
        # If trade price better than our quote, clear queue
        if (bid and trade.price < order.price) or (ask and trade.price > order.price):
            order.queue_ahead_qty = 0.0
        
        # Consume queue
        queue_consumed = min(order.queue_ahead_qty, available_qty)
        order.queue_ahead_qty -= queue_consumed
        available_qty -= queue_consumed
        
        if available_qty <= 0: return  # Queue not cleared yet
    
    # 4. Fill ourorder
    fill_qty = min(order.remaining_qty, available_qty)
    order.remaining_qty -= fill_qty
    
    # 5. Update portfolio
    portfolio.on_fill(side, price, fill_qty, fee)
    
    # 6. Record fill
    fills.append({...})
    
    # 7. If fully filled, clear slot
    if order.remaining_qty < 1e-12:
        order.status = "FILLED"
        _bid or _ask = None
```

**INPUTS**:
- `trade`: Real Binance aggTrade event (price, qty, is_buyer_maker, timestamp)
- `order`: Our resting ShadowOrder (price, qty, remaining_qty, queue_ahead_qty, active_at)
- `assumptions`: ExecutionAssumptions (maker_fee_rate, latency_ms, fill_ratio, queue_model)

**ASSUMPTIONS**:
1. **Crossing = immediate fill** (optimistic)
2. **Queue consumed linearly** when `queue_ahead` enabled
3. **No market impact** of own orders
4. **Binary partial fills** (either fill or don't, proportional to trade.qty)
5. **No GTX rejection** (post-only not enforced)
6. **No order book interaction** beyond queue_ahead snapshot

**OUTPUT**:
- Fill record with qty, price, fee, PnL
- Updated order.remaining_qty
- Updated portfolio state

**LIMITATIONS**:
1. ❌ No probabilistic queue position
2. ❌ No queue evolution between trades
3. ❌ No hidden liquidity
4. ❌ No self-trade prevention
5. ❌ No order arrival latency (only activation gate)
6. ❌ No response latency
7. ❌ No post-only rejection modeling

---

## 3. Current Execution Model Detailed Answers

### A. How is an order created?
**Location**: `ShadowQuoteEngine.update_quotes()` lines 205-242

Strategy returns `QuotePair(bid, ask)` → Risk validates → Engine creates `ShadowOrder`:
```python
ShadowOrder(
    order_id=next_order_id,
    side="bid" or "ask",
    price=quotes.bid.price or quotes.ask.price,
    qty=quotes.bid.qty or quotes.ask.qty,
    remaining_qty=qty,  # Initially full
    placed_at=timestamp,
    active_at=timestamp + assumptions.latency_ms,
    queue_ahead_qty=book.bids.get(price, 0.0) or book.asks.get(price, 0.0)
)
```

### B. How is it represented?
**Location**: `ShadowOrder` dataclass lines 67-88

```python
@dataclass
class ShadowOrder:
    order_id: int
    side: str  # "bid" or "ask"
    price: float
    qty: float  # Original
    remaining_qty: float  # Unfilled
    placed_at: int  # Timestamp (ms)
    active_at: int  # placed_at + latency_ms
    queue_ahead_qty: float = 0.0  # From L2 snapshot
    activation_logged: bool = False
    status: str = "WAITING"  # → "ACTIVE" → "FILLED"/"CANCELED"/"EXPIRED"
    canceled_at: int | None = None
```

### C. How does it enter ShadowQuoteEngine?
**Location**: `ShadowQuoteEngine.update_quotes()` lines 148-248

Called from `replay.py` line 312:
```python
broker.update_quotes(quotes, timestamp, bid_ahead, ask_ahead)
```

Where `bid_ahead` = `book.bids.get(quotes.bid.price, 0.0)` - **L2 quantity AT quote price**

### D. How does simulator decide it's filled?
**Location**: `ShadowQuoteEngine.on_trade()` + `_fill()` lines 313-462

**Conditions** (ALL must be true):
1. Order exists (`_bid` or `_ask` not None)
2. Trade direction matches (is_buyer_maker → fills bid, !is_buyer_maker → fills ask)
3. Latency elapsed (`timestamp >= order.active_at`)
4. Price crosses (`trade.price <= bid.price` or `trade.price >= ask.price`)
5. If queue_ahead: queue consumed first, then fills if `available_qty > 0`

### E. How are partial fills handled?
**Location**: `_fill()` lines 380-390, 439-446

```python
fill_qty = min(order.remaining_qty, available_qty)
order.remaining_qty -= fill_qty

if fill_qty < order.qty:  # Original qty
    emit("PARTIAL_FILL", ...)

if order.remaining_qty < 1e-12:
    order.status = "FILLED"
    _bid/_ask = None  # Fully filled, clear slot
```

**Binary**: Either fills completely in one trade, or partially and waits for next trade.

### F. How are cancellations handled?
**Location**: `update_quotes()` lines 176-191, 219-234

**Cancel-replace on price/qty change**:
```python
if existing_order.price != new_quote.price or existing_order.qty != new_quote.qty:
    cancel(existing_order)
    create_new_order()
```

**Expiration** (if `max_quote_age_ms` set):
```python
if timestamp - order.placed_at > max_quote_age_ms:
    order.status = "EXPIRED"
    _bid/_ask = None
```

### G. How is order lifetime represented?
**States**: `"WAITING"` → `"ACTIVE"` → `"FILLED"`/`"CANCELED"`/`"EXPIRED"`

**Timestamps**:
- `placed_at`: When strategy decided to quote
- `active_at`: `placed_at + latency_ms` (earliest fill time)
- `canceled_at`: When cancel occurred (if applicable)
- Fill timestamp: `trade.event_time` when filled

### H. How does market data affect existing resting order?
**Location**: `on_trade()` + `_fill()` lines 313-462

1. **Trade crosses price**: Triggers fill check
2. **Trade at our price**: 
   - `queue_ahead` model: Consumes queue, may trigger fill
   - `shadow` model: Fills immediately (optimistic)
3. **Trade through our price** (better): Clears queue instantly (`queue_ahead_qty = 0`)
4. **Depth update**: Only affects NEW orders via `bid_ahead`/`ask_ahead` snapshot

**Current limitation**: Queue ahead is STATIC after order placement. Does NOT update between trades.

### I. Does queue position currently exist?
**YES** - Primitive implementation:

```python
queue_ahead_qty = book.bids.get(our_price, 0.0)  # Snapshot at placement
```

**When `queue_model = "queue_ahead"`**:
- Tracks qty ahead
- Consumes linearly with trades
- Clears if trade price better than ours

**Limitation**: Does NOT model probabilistic position, just linear consumption.

### J. Does latency currently exist?
**YES** - Simple delay model:

```python
active_at = placed_at + latency_ms
```

**Gate in on_trade()**:
```python
if timestamp >= order.active_at:
    _fill(...)
```

**Limitation**: 
- Single fixed latency value
- No separate feed/order/response latencies
- Not measured, just configured

### K. Does execution use actual observed trades?
**YES** - Primary fill trigger:

```python
def on_trade(self, trade: TradeTick):
    # trade = Real Binance aggTrade event
    if trade.price crosses our_order.price:
        _fill(...)
```

**Correct**: Uses `TradeTick` from Parquet, NOT synthetic.

### L. Does execution use actual observed L2 depth?
**PARTIALLY**:

**YES for queue_ahead snapshot**:
```python
bid_ahead = book.bids.get(price, 0.0)  # Real L2 qty
```

**NO for queue evolution**: Static after placement, doesn't track depth changes between trades.

### M. Where are assumptions substituted for observations?
1. **Queue position**: Assumes linear consumption, no probabilistic model
2. **Latency**: Configured constant, not measured per-event
3. **Fill ratio**: Multiplier on trade qty (`fill_ratio = 1.0` default)
4. **No hidden liquidity**: Only uses visible L2 depth
5. **No market impact**: Our orders don't affect market
6. **Immediate crossing**: No partial queue depletion modeling
7. **No GTX rejection**: Doesn't model post-only mechanics

---

## 4. HFTENGINE Execution Architecture (REFERENCE)

### Pipeline (from HFTENGINE_REFERENCE_ANALYSIS.md)

```
Binance feed (depth@0ms, trade, bookTicker)
    ↓ Measured feed latency
collector (hftbacktest)
    ↓
convert() → .npz
    ↓
derive order latency (feed_latency * mul_entry/mul_resp)
    ↓
IntpOrderLatency (interpolated latency at any time)
    ↓
hftbacktest Runner (Rust)
    ├── NoPartialFillExchange
    │   ├── GTX (post-only) orders
    │   ├── Arrival after entry_latency
    │   ├── Response after response_latency
    │   └── Queue position tracked
    ├── ProbQueueModel
    │   ├── PowerProbQueueFunc3(n=3)
    │   ├── front_q_qty evolution
    │   └── Probabilistic fill
    └── Strategy
        ↓
.hbr recording (frame-by-frame state)
    ↓
Dashboard replay
```

**Key Differences from LOB-X**:
1. **Measured latency** (feed) + **derived latency** (order)
2. **Probabilistic queue**: `prob = (front_qty / level_qty)^n`
3. **GTX modeling**: Post-only rejection when crosses
4. **Separate latencies**: feed, entry, response
5. **Frame recording**: Full state capture for replay

---

## 5. Queue Model Comparison

### LOB-X Current: Linear Consumption

```python
queue_ahead_qty = book.bids.get(price, 0.0)  # Snapshot

# On each trade at our price:
queue_consumed = min(queue_ahead_qty, available_qty)
queue_ahead_qty -= queue_consumed
```

**Interpretation**: "Quantity ahead in line, consumed first-come-first-served"

**Problems**:
1. Static snapshot - doesn't update with depth changes
2. Assumes we're last in queue (pessimistic)
3. No probabilistic element
4. Doesn't account for hidden liquidity or cancellations ahead

### HFTENGINE: Probabilistic Power Law

```rust
fn prob(n: f64, level_qty: f64, front_qty: f64) -> f64 {
    (front_qty / level_qty).powf(n)
}
```

Where:
- `level_qty` = Total visible quantity at price level
- `front_qty` = Estimated quantity ahead of us
- `n` = Power exponent (default 3)
- `prob` = Probability we're still waiting (haven't filled)

**Example** (n=3):
- `front_qty = 0` (first in queue) → `prob = 0` → **fill now**
- `front_qty = 0.5 * level_qty` → `prob = 0.125` → **87.5% filled**
- `front_qty = level_qty` (last) → `prob = 1.0` → **0% filled**

**Queue Evolution**:
```rust
// Trade at our price level
new_front_qty = front_qty - trade_qty
if new_front_qty <= 0:
    // We filled!
    fill_event()
```

**Calibration**: Adjust `n` by comparing backtest fills to live fills.

---

## 6. Latency Model Comparison

### LOB-X Current: Single Constant

```python
active_at = placed_at + latency_ms
```

**Configuration**: `ExecutionAssumptions(latency_ms=0)` (default)

**Gate**: `if timestamp >= order.active_at: fill()`

**Limitations**:
1. No feed latency tracking
2. No separate order entry/response
3. No per-event variation
4. No measurement from data

### HFTENGINE: Measured + Derived

**Feed Latency** (MEASURED):
```python
feed_latency = local_receipt_ns - exchange_timestamp_ns
```

**Stored per event** in raw capture

**Order Latency** (DERIVED):
```python
order_entry_latency = feed_latency * entry_multiplier  # Default 1x
order_response_latency = feed_latency * response_multiplier  # Default 1x
```

**Usage**:
```rust
// Order submission
exchange_arrival_time = current_time + order_entry_latency

// Order confirmation
strategy_sees_fill = fill_time + order_response_latency
```

**Advantages**:
1. Grounded in real network measurements
2. Varies per event (realistic)
3. Separate entry/response paths
4. Configurable multipliers for sensitivity analysis

**Limitation**: Still assumes order path ≈ feed path

---

## 7. Fill Model Comparison

### LOB-X: Binary Crossing

```
IF (trade.price crosses order.price AND
    timestamp >= active_at AND
    queue consumed):
    THEN fill min(remaining_qty, available_qty)
```

**Binary**: Fill or don't fill, proportional to trade qty.

**No GTX**: Order doesn't reject even if crosses spread.

### HFTENGINE: GTX + Probabilistic

```
1. Order arrives at exchange after entry_latency
2. IF order would immediately execute:
       REJECT (GTX post-only)
   ELSE:
       JOIN queue at back (front_qty = level_qty)
3. On each trade at price level:
       front_qty -= trade_qty
       fill_prob = 1 - (front_qty / level_qty)^n
       IF random() < fill_prob:
           FILL (fully, no partials)
4. Confirmation arrives after response_latency
```

**Probabilistic**: Queue position evolves, fill depends on random draw.

**GTX**: Explicitly models post-only rejection.

**No Partials**: `NoPartialFillExchange` - binary fill like LOB-X.

---

## 8. Event Model Comparison

### LOB-X Current Events

**Observed** (from Binance):
- `DepthUpdate` (side, price, qty, timestamp)
- `TradeTick` (price, qty, is_buyer_maker, trade_id, timestamp)
- (implicitly: `BookTicker` for best bid/ask)

**Simulated** (ShadowQuoteEngine):
- `ORDER_CREATED`
- `ORDER_WAITING` (before active_at)
- `ORDER_ACTIVE` (after latency)
- `FILL` / `PARTIAL_FILL`
- `ORDER_CANCELED`
- `ORDER_EXPIRED`
- `TRADE_RECEIVED`
- `QUEUE_UPDATED` (if queue_ahead)
- `QUOTE_CALCULATED` (strategy decision audit)
- `MARKOUT` (post-fill analysis)

**Missing**:
- ❌ `ORDER_REJECTED` (GTX rejection)
- ❌ `ORDER_ACCEPTED` (exchange confirms placement)
- ❌ Separate entry/response latency events
- ❌ Queue position evolution events

### HFTENGINE Events

**Observed**:
- Depth updates
- Trades
- BookTicker

**Simulated**:
- Order submission
- Order arrival (after entry_latency)
- GTX acceptance/rejection
- Queue join
- Queue evolution (each trade)
- Probabilistic fill check
- Fill execution
- Fill confirmation (after response_latency)

**Recorded in .hbr**:
- Every state transition
- Queue positions for all orders
- Latency values at each moment
- Strategy decisions

---

## 9. Observed vs. Inferred Data

### OBSERVED (Available in LOB-X Data)

From Binance WebSocket/Parquet:
- ✅ Exchange timestamp (`E` field, ms)
- ✅ Local receipt timestamp (collector)
- ✅ Trade price, qty, side (is_buyer_maker)
- ✅ Trade ID
- ✅ Depth updates (price, qty, side)
- ✅ Best bid/ask (bookTicker or computed)
- ✅ L2 aggregated quantities

**Measurable** (can compute):
- ✅ Feed latency (`local_receipt - exchange_timestamp`)
- ✅ Mid-price
- ✅ Spread
- ✅ Imbalance
- ✅ Microprice
- ✅ Realized volatility
- ✅ Trade intensity

### INFERRED (Requires Estimation)

**Cannot observe from public data**:
- ❌ Exact queue position (no order IDs in L2)
- ❌ Which specific orders ahead canceled
- ❌ Hidden liquidity (iceberg orders)
- ❌ Order arrival time at exchange (only submission + model)
- ❌ Actual fill priority among equal-price orders
- ❌ Market maker vs taker identification (heuristic only)
- ❌ Self-trade prevention effects
- ❌ Exchange matching engine internal state

**Must Model**:
- Queue position: Probabilistic estimate
- Order latency: Derived from feed latency or configured
- Fill probability: Based on queue model
- GTX behavior: Simulate exchange rules

**Critical**: Always distinguish MEASURED from MODELED in results.

---

## 10. Proposed LOB-X Execution Architecture

### Principles
1. **Preserve zero-latency baseline** for deterministic experiments
2. **Separate measurement from estimation**
3. **Incremental migration** - don't break existing experiments
4. **Explicit assumptions** - document what's modeled vs observed
5. **Backwards compatible** - old configs still work

### Proposed Structure

```
MARKET DATA
    ├── Measured: exchange_ts, local_receipt_ts, trades, depth
    └── Computed: feed_latency = local_receipt - exchange_ts
        ↓
REPLAY
    ├── LocalOrderBook (L2 mirror)
    ├── Latency tracker (per-event feed latency)
    └── MarketState.from_book()
        ↓
STRATEGY
    ↓
RISK
    ↓
EXECUTION MODEL (Enhanced ShadowQuoteEngine)
    ├── Order submission (timestamp_submitted)
    ├── Order arrival modeling:
    │   ├── Mode 0: instant (baseline, latency=0)
    │   ├── Mode 1: fixed delay (current, latency_ms)
    │   └── Mode 2: derived latency (feed_latency * mul_entry)
    ├── GTX check (optional):
    │   └── IF order crosses spread: REJECT
    ├── Queue join:
    │   ├── Snapshot: level_qty from L2
    │   ├── Position: back of queue initially
    │   └── Model: "linear" (current) or "probabilistic" (new)
    ├── Queue evolution:
    │   ├── Track front_qty per order
    │   ├── Update on each trade at price
    │   └── Emit QUEUE_UPDATED events
    ├── Fill decision:
    │   ├── Linear: front_qty <= 0 → fill
    │   └── Probabilistic: draw ~ Uniform(0,1), fill if < fill_prob
    └── Response latency (optional):
        └── Strategy sees fill after response_delay
            ↓
PORTFOLIO
    ↓
ANALYTICS
```

### Configuration Evolution

**Current** (`ExecutionAssumptions`):
```python
maker_fee_rate: float = 0.0
latency_ms: int = 0
fill_ratio: float = 1.0
queue_model: str = "shadow"  # or "queue_ahead"
max_quote_age_ms: int | None = None
```

**Proposed** (backwards compatible):
```python
maker_fee_rate: float = 0.0

# Latency
latency_mode: str = "instant"  # instant | fixed | derived
latency_ms: int = 0  # Used if mode=fixed
entry_multiplier: float = 1.0  # Used if mode=derived
response_multiplier: float = 1.0  # Used if mode=derived

# Queue
queue_model: str = "shadow"  # shadow | linear | probabilistic
queue_power: float = 3.0  # n for probabilistic model
fill_ratio: float = 1.0  # Deprecated, use queue_model

# Post-only
enforce_gtx: bool = False  # Reject if crosses spread

# Expiration
max_quote_age_ms: int | None = None
```

**Migration**:
- `latency_ms > 0` → auto-set `latency_mode="fixed"`
- `queue_model="queue_ahead"` → map to `queue_model="linear"`
- `queue_model="shadow"` → stays instant optimistic

---

## 11. Proposed Queue Model

### Design: Three Modes

#### Mode 1: "shadow" (Current Default)
**Keep as-is** for backwards compatibility.

```python
if queue_model == "shadow":
    # Instant optimistic fill
    if trade.price crosses order.price:
        fill immediately
```

**Use case**: Baseline, maximum-performance reference.

#### Mode 2: "linear" (Current "queue_ahead")
**Keep with enhancements**.

```python
if queue_model == "linear":
    # Initialize
    front_qty = book.get_level_qty(order.price)  # L2 snapshot
    
    # On each trade
    if trade.price == order.price:
        front_qty -= trade.qty
        if front_qty <= 0:
            fill_qty = min(order.remaining_qty, abs(front_qty))
            fill(order, fill_qty)
    
    elif trade.price better than order.price:
        front_qty = 0  # Cleared through
        check_fill()
```

**Enhancement**: Also update `front_qty` on depth changes at our price (not just trades).

**Use case**: Conservative estimate, pessimistic bias.

#### Mode 3: "probabilistic" (NEW - HFTENGINE-inspired)
**Implement following HFTENGINE pattern**.

```python
if queue_model == "probabilistic":
    # Initialize
    level_qty = book.get_level_qty(order.price)
    front_qty = level_qty  # Start at back
    n = queue_power  # Default 3.0
    
    # On each trade at our price
    front_qty -= trade.qty
    
    if front_qty <= 0:
        # Definitely fill
        fill(order, min(order.remaining_qty, trade.qty))
    
    else:
        # Probabilistic fill
        fill_prob = 1.0 - (front_qty / level_qty) ** n
        if random.uniform(0, 1) < fill_prob:
            fill(order, min(order.remaining_qty, trade.qty))
```

**Calibration**: 
- Start with `n=3` (HFTENGINE default)
- Run experiments, measure actual fill rate
- Adjust `n` if systematic bias observed
- Document calibration in experiment metadata

**Use case**: Realistic estimate with tunable optimism.

### Required State (ShadowOrder Enhancement)

```python
@dataclass
class ShadowOrder:
    # Existing fields
    order_id: int
    side: str
    price: float
    qty: float
    remaining_qty: float
    placed_at: int
    active_at: int
    status: str
    canceled_at: int | None
    activation_logged: bool
    
    # Queue model state (enhanced)
    queue_model: str  # "shadow" | "linear" | "probabilistic"
    queue_ahead_qty: float = 0.0  # For linear/prob: front_qty
    queue_level_qty: float = 0.0  # For prob: total at level
    queue_power: float = 3.0  # For prob: n exponent
    queue_updated_at: int | None = None  # Last update timestamp
```

---

## 12. Proposed Latency Model

### Design: Three Modes

#### Mode 1: "instant" (Current Default with latency_ms=0)
**Keep as-is**.

```python
active_at = placed_at  # No delay
```

**Use case**: Deterministic baseline, strategy evaluation without execution noise.

#### Mode 2: "fixed" (Current with latency_ms > 0)
**Keep as-is**.

```python
active_at = placed_at + latency_ms
```

**Use case**: Sensitivity analysis, simple delay model.

#### Mode 3: "derived" (NEW - HFTENGINE-inspired)
**Derive from measured feed latency**.

**Requirements**:
1. Measure feed latency during collection:
   ```python
   feed_latency_ms = local_receipt_ms - exchange_timestamp_ms
   ```
2. Store in Parquet or separate file
3. Interpolate latency at strategy decision time
4. Apply multipliers:
   ```python
   order_entry_latency = feed_latency * entry_multiplier
   order_response_latency = feed_latency * response_multiplier
   ```

**Implementation**:
```python
class LatencyTracker:
    """Tracks measured feed latency and derives order latency."""
    
    def __init__(self, mode: str, fixed_ms: int, entry_mul: float, resp_mul: float):
        self.mode = mode
        self.fixed_ms = fixed_ms
        self.entry_mul = entry_mul
        self.resp_mul = resp_mul
        self._feed_latencies: list[tuple[int, float]] = []  # (timestamp, latency_ms)
    
    def observe_feed_latency(self, timestamp: int, latency_ms: float):
        """Record measured feed latency at this timestamp."""
        self._feed_latencies.append((timestamp, latency_ms))
    
    def get_order_entry_latency(self, timestamp: int) -> int:
        """Get order entry latency at this timestamp."""
        if self.mode == "instant":
            return 0
        elif self.mode == "fixed":
            return self.fixed_ms
        elif self.mode == "derived":
            feed_lat = self._interpolate_feed_latency(timestamp)
            return int(feed_lat * self.entry_mul)
    
    def get_order_response_latency(self, timestamp: int) -> int:
        """Get order response latency at this timestamp."""
        if self.mode == "instant":
            return 0
        elif self.mode == "fixed":
            return self.fixed_ms  # Or separate config
        elif self.mode == "derived":
            feed_lat = self._interpolate_feed_latency(timestamp)
            return int(feed_lat * self.resp_mul)
    
    def _interpolate_feed_latency(self, timestamp: int) -> float:
        """Linear interpolation of feed latency."""
        # Find surrounding measurements
        # Return interpolated value
        pass
```

**Data Requirements**:
- Must record `local_receipt_timestamp` during collection
- Add to Parquet schema or separate `.latency.npz` file
- Handle gaps/outliers (smooth, clip extremes)

**Use case**: Most realistic, grounded in actual network measurements.

---

## 13. Proposed Partial Fill Lifecycle

### Current: Binary Full/Partial

```python
fill_qty = min(order.remaining_qty, available_qty)
order.remaining_qty -= fill_qty

if order.remaining_qty < 1e-12:
    order.status = "FILLED"
    clear_order_slot()
else:
    # Remains in place for next fill opportunity
    pass
```

**Works correctly** - no change needed.

### Proposal: Explicit States

**Current**:
- `"WAITING"` → `"ACTIVE"` → `"FILLED"`/`"CANCELED"`/`"EXPIRED"`

**Proposed (optional enhancement)**:
- `"WAITING"` → `"ACTIVE"` → `"PARTIAL"` → `"FILLED"`/`"CANCELED"`/`"EXPIRED"`

**Benefit**: Clearer event trail, easier analytics.

**Implementation**:
```python
def _fill(self, order, trade):
    # ... existing logic ...
    
    if fill_qty < order.remaining_qty:
        order.status = "PARTIAL"  # Explicitly mark
        emit("PARTIAL_FILL", ...)
    else:
        order.status = "FILLED"
        emit("FILL_COMPLETE", ...)
        clear_order_slot()
```

**Not critical** - current implementation already handles partials correctly.

---

## 14. Required Data

### Currently Available ✅
- Exchange timestamps (ms)
- Local receipt timestamps (if collected - **verify**)
- Trade events (price, qty, side, trade_id)
- Depth updates (side, price, qty)
- Best bid/ask (computed or from bookTicker)
- Symbol metadata

### Missing / Needs Verification ⚠️
- **Local receipt timestamps**: Check if `collector.py` records these
- **Feed latency per event**: Compute if timestamps available
- **Continuous latency series**: For interpolation in derived mode

### To Add for Full Realism 📋
1. **Feed Latency Measurement**:
   - Modify collector to record `local_receipt_ns`
   - Compute `feed_latency_ms = (local - exchange) / 1e6`
   - Store in Parquet or separate file

2. **Latency Distribution**:
   - Analyze feed latency over session
   - Identify stable periods vs. degraded periods
   - Flag outliers/gaps for session quality

3. **Queue Calibration Data** (future):
   - If live trading: actual fill timestamps
   - Compare live vs backtest fill rates
   - Calibrate queue_power `n`

### Synthetic Data Limitations

**Check**: Does LOB-X still use synthetic depth in any experiments?

**If YES**:
- ❌ Cannot use queue models (no real L2)
- ❌ Cannot measure latency (no timestamps)
- ✅ Can still test strategy logic
- ✅ Useful for unit tests

**Action**: Document which experiments use real vs synthetic data.

---

## 15. Required Calibration

### Queue Model (Probabilistic)

**Parameter**: `queue_power` (n)

**Method**:
1. Run backtest with `n=3` (HFTENGINE default)
2. Record: 
   - Number of fills
   - Average time-to-fill
   - Queue positions at fill time
3. If live trading available:
   - Compare live fill rate to backtest
   - Adjust `n` to match (higher n = more optimistic)
4. Document calibration in experiment metadata

**Validation**:
- Fill rate should be bounded by:
  - Upper bound: `queue_model="shadow"` (instant fills)
  - Lower bound: `queue_model="linear"` (pessimistic)
- Probabilistic should fall in between

### Latency Model (Derived)

**Parameters**: `entry_multiplier`, `response_multiplier`

**Method**:
1. Measure feed latency from data
2. If live trading available:
   - Place far-from-mid orders
   - Immediately cancel
   - Measure round-trip time
   - Compare to feed latency
   - Derive multipliers
3. Default to `1x/1x` (conservative assumption)

**Validation**:
- Compare HTTPS keep-alive round trip to `2 * feed_latency`
- Should be similar order of magnitude

### DO NOT Optimize for PnL

**Critical Rule**: Never tune parameters to improve backtest PnL.

Calibration must be based on:
- ✅ Matching live fill rates
- ✅ Matching live latencies
- ✅ Consistency with measured data
- ❌ NOT better Sharpe ratio
- ❌ NOT higher profits
- ❌ NOT lower drawdown

**Why**: Execution model is about **realism**, not **performance tuning**.

---

## 16. Experiment Matrix

### Proposed Experiments (After Implementation)

| Exp | queue_model | latency_mode | gtx | Purpose |
|-----|-------------|--------------|-----|---------|
| A | shadow | instant | False | **Baseline** - Deterministic, optimistic |
| B | linear | instant | False | Queue only, no latency |
| C | shadow | fixed (50ms) | False | Latency only, no queue |
| D | linear | fixed (50ms) | False | Queue + fixed latency |
| E | probabilistic (n=3) | instant | False | Probabilistic queue only |
| F | probabilistic (n=3) | derived (1x/1x) | False | **Realistic** - Queue + latency |
| G | probabilistic (n=3) | derived (1x/1x) | True | Full realism + GTX |

### Metrics to Compare

**Execution**:
- Fill count
- Partial fill count
- GTX rejection count (if applicable)
- Average time-to-fill
- Fill rate (fills / quotes)

**Performance**:
- Gross PnL
- Fees
- Net PnL
- Turnover
- Inventory extremes

**Risk**:
- Max drawdown
- Sharpe-like ratio
- Markout (100ms, 1s, 10s horizons)
- Adverse selection

**Goal**: Understand how execution assumptions affect results, NOT find best assumptions.

---

## 17. Test Plan

### Unit Tests (Execution Model)

#### Queue Model Tests
```python
def test_shadow_queue_instant_fill():
    """Shadow model fills immediately when trade crosses."""
    
def test_linear_queue_waits_for_consumption():
    """Linear model waits until queue consumed."""
    
def test_linear_queue_clears_on_through_trade():
    """Trade through our price clears queue instantly."""
    
def test_probabilistic_queue_eventually_fills():
    """Probabilistic model fills with correct probability."""
    
def test_probabilistic_queue_power_affects_probability():
    """Higher n = more optimistic fills."""
```

#### Latency Tests
```python
def test_instant_latency_fills_immediately():
    """Mode=instant bypasses latency gate."""
    
def test_fixed_latency_delays_fill():
    """Mode=fixed waits latency_ms before fill."""
    
def test_derived_latency_uses_feed_measurement():
    """Mode=derived scales feed_latency by multiplier."""
    
def test_order_not_active_until_latency_elapsed():
    """Order at placed_at < t < active_at cannot fill."""
```

#### GTX Tests
```python
def test_gtx_rejects_crossing_order():
    """enforce_gtx=True rejects order that crosses spread."""
    
def test_gtx_accepts_non_crossing_order():
    """enforce_gtx=True accepts order inside spread."""
    
def test_no_gtx_allows_crossing():
    """enforce_gtx=False allows aggressive orders (current behavior)."""
```

#### Partial Fill Tests
```python
def test_partial_fill_updates_remaining_qty():
    """Partial fill decreases remaining_qty correctly."""
    
def test_multiple_partials_eventually_complete():
    """Multiple small fills eventually complete order."""
    
def test_fully_filled_order_clears_slot():
    """Order slot cleared when remaining_qty == 0."""
```

### Integration Tests (Replay)

```python
def test_baseline_experiment_deterministic():
    """Exp A (shadow/instant) produces same results each run."""
    
def test_latency_reduces_fill_rate():
    """Exp C (latency) has fewer fills than Exp A (baseline)."""
    
def test_queue_reduces_fill_rate():
    """Exp B (queue) has fewer fills than Exp A (baseline)."""
    
def test_realistic_experiment_most_conservative():
    """Exp F (queue+latency) has fewest fills."""
```

### Accounting Tests

```python
def test_partial_fills_update_inventory_correctly():
    """Inventory tracker handles partial fills."""
    
def test_fees_calculated_on_filled_qty_only():
    """Fees based on actual fill, not original qty."""
    
def test_realized_pnl_includes_all_partials():
    """Realized PnL accounts for all partial fills."""
    
def test_markout_uses_fill_price_not_quote_price():
    """Markout measured from actual fill, not quote."""
```

---

## 18. Risks and Limitations

### Implementation Risks

1. **Breaking Existing Experiments** 🔴 HIGH
   - Mitigation: Backwards-compatible config, explicit defaults
   - Test: Run old experiments with new code, verify identical results

2. **Complexity Increase** 🟡 MEDIUM
   - Multiple queue models, latency modes, GTX logic
   - Mitigation: Clear mode separation, comprehensive tests
   - Document decision matrix for users

3. **Calibration Required** 🟡 MEDIUM
   - Probabilistic model needs tuning
   - Mitigation: Provide sensible defaults (n=3), document calibration
   - Users can start with simpler models

4. **Data Requirements** 🟡 MEDIUM
   - Derived latency needs feed_latency measurements
   - Mitigation: Fallback to fixed mode if data missing
   - Document data schema requirements

5. **Randomness in Results** 🟢 LOW
   - Probabilistic fills introduce variance
   - Mitigation: Set random seed for reproducibility
   - Run multiple seeds, report distribution

### Model Limitations

1. **Queue Position Still Estimated** 🔴 FUNDAMENTAL
   - Cannot observe true FIFO position from L2
   - Probabilistic model is still approximate
   - Hidden liquidity unknown
   - **Accept as inherent limitation of L2 data**

2. **No Market Impact** 🟡 ACCEPTABLE
   - Our orders don't move market
   - Valid for small size relative to book
   - Document size assumptions

3. **No Self-Trade Prevention** 🟢 MINOR
   - Rare in practice (requires crossing own quote)
   - Can add if needed later

4. **Binary Partial Fills** 🟢 ACCEPTABLE
   - HFTENGINE also has `NoPartialFillExchange`
   - Partial queue depletion is complex
   - Current binary model is reasonable approximation

5. **Static Multipliers** 🟢 ACCEPTABLE
   - Latency multipliers don't adapt
   - Good enough for most experiments
   - Can enhance later with time-varying model

### Research Limitations

1. **Cannot Prove Correctness** 🔴 FUNDAMENTAL
   - No ground truth for queue position
   - Cannot validate without live trading
   - **Accept as simulation limitation**

2. **Parameter Sensitivity** 🟡 IMPORTANT
   - Results depend on queue_power, multipliers
   - Mitigation: Run sensitivity analysis
   - Report results across parameter range

3. **Regime Dependence** 🟡 IMPORTANT
   - Model calibrated on one market regime
   - May not generalize to different conditions
   - Document calibration context

---

## 19. Exact Files Needing Modification

### Phase 1: Queue Model Enhancement

**Modify**:
1. `backend/python/lobx/execution/shadow_engine.py`
   - Add `queue_model="probabilistic"` logic to `_fill()`
   - Add `queue_power` parameter to `ExecutionAssumptions`
   - Enhance `ShadowOrder` with queue state fields
   - Add probabilistic fill decision

**Create**:
2. `backend/python/lobx/execution/queue.py` (NEW)
   - `def probabilistic_fill_probability(front_qty, level_qty, n) -> float`
   - `class QueueModel(ABC)` with subclasses: `ShadowQueue`, `LinearQueue`, `ProbabilisticQueue`
   - Keep logic modular for testing

**Test**:
3. `backend/python/tests/test_queue_models.py` (NEW)
   - Unit tests for each queue model
   - Probabilistic fill distribution tests

**DON'T Touch**:
- `strategy/` - No changes needed
- `portfolio/` - No changes needed
- `analytics/` - No changes needed

### Phase 2: Latency Model Enhancement

**Modify**:
1. `backend/python/lobx/execution/shadow_engine.py`
   - Add `latency_mode` to `ExecutionAssumptions`
   - Add `entry_multiplier`, `response_multiplier`
   - Integrate `LatencyTracker`

**Create**:
2. `backend/python/lobx/execution/latency.py` (NEW)
   - `class LatencyTracker` (from design section 12)
   - Feed latency interpolation
   - Order latency derivation

3. `backend/python/lobx/market_data/latency.py` (NEW - optional)
   - Feed latency measurement during collection
   - Latency data schema
   - Quality checks (detect outliers, gaps)

**Modify**:
4. `backend/python/lobx/backtest/replay.py`
   - Pass `LatencyTracker` to `ShadowQuoteEngine`
   - Feed latency measurements to tracker

**Test**:
5. `backend/python/tests/test_latency_models.py` (NEW)
   - Test each latency mode
   - Test interpolation
   - Test multiplier scaling

### Phase 3: GTX (Post-Only) Enforcement

**Modify**:
1. `backend/python/lobx/execution/shadow_engine.py`
   - Add `enforce_gtx` to `ExecutionAssumptions`
   - Add GTX check in `update_quotes()`:
     ```python
     if enforce_gtx and order_crosses_spread(order, market_bid, market_ask):
         reject_order(order, reason="GTX_WOULD_CROSS")
         return
     ```

**Test**:
2. `backend/python/tests/test_gtx_enforcement.py` (NEW)
   - Test rejection when crossing
   - Test acceptance when not crossing
   - Test disabled GTX allows crossing

### Phase 4: Integration and Validation

**Modify**:
1. `backend/python/lobx/backtest/replay.py`
   - Pass latency measurements
   - Configure execution assumptions from `BacktestConfig`

2. `scripts/run_backtest.py`
   - Add CLI flags for execution model selection
   - `--queue-model`, `--latency-mode`, `--gtx`, etc.

**Create**:
3. `scripts/run_execution_experiments.py` (NEW)
   - Run experiment matrix (Exp A-G)
   - Compare results
   - Generate comparison report

**Test**:
4. `backend/python/tests/test_execution_integration.py` (NEW)
   - End-to-end backtest with each model
   - Verify accounting correctness
   - Compare experiment results

### Files NOT to Touch (Until Later)

- ❌ `_reference/hftengine/` - Read-only forever
- ❌ `backend/cpp/` - No C++ changes needed
- ❌ `strategy/` - Strategies don't change
- ❌ `models/` - Mathematical models unchanged
- ❌ `analytics/` - Analytics consumers, not producers
- ❌ `risk/` - Risk applies after strategy, before execution
- ❌ `live/` - Live trading separate (future)

---

## 20. Recommended Implementation Order

### Step 1: Probabilistic Queue Model (1-2 days)

**Goal**: Add `queue_model="probabilistic"` alongside existing models.

**Tasks**:
1. Create `execution/queue.py`:
   ```python
   def prob_fill(front_qty: float, level_qty: float, n: float) -> float:
       if front_qty <= 0: return 1.0
       if level_qty <= 0: return 0.0
       return 1.0 - (front_qty / level_qty) ** n
   ```

2. Enhance `ExecutionAssumptions`:
   ```python
   queue_model: str = "shadow"  # shadow | linear | probabilistic
   queue_power: float = 3.0
   ```

3. Modify `ShadowEngine._fill()`:
   ```python
   if assumptions.queue_model == "probabilistic":
       fill_prob = prob_fill(order.queue_ahead_qty, order.queue_level_qty, assumptions.queue_power)
       if random.random() < fill_prob:
           _execute_fill(...)
   ```

4. Add tests: `test_queue_models.py`

5. Run experiment: Compare shadow vs linear vs probabilistic

**Files**:
- NEW: `execution/queue.py`
- MODIFY: `execution/shadow_engine.py`
- NEW: `tests/test_queue_models.py`

**Validation**:
- ✅ Shadow fills most
- ✅ Linear fills least
- ✅ Probabilistic fills in between
- ✅ Higher n → more fills

**Mathematical Model**:
```
prob_fill = 1 - (front_qty / level_qty)^n
```

**Data**: Uses existing L2 snapshots (no new data needed).

### Step 2: Feed Latency Measurement (1 day)

**Goal**: Measure feed latency from existing data OR add to collector.

**Tasks**:
1. Check if `local_receipt_timestamp` already collected
2. If YES:
   - Compute `feed_latency = local - exchange`
   - Store in Parquet or separate file
3. If NO:
   - Modify `tools/collect.py` or `collector` to record
   - Rerun collection for new dataset

4. Create `execution/latency.py`:
   ```python
   class FeedLatencyTracker:
       def load_from_parquet(path): ...
       def get_latency_at(timestamp): ...  # Interpolate
   ```

**Files**:
- CHECK: `tools/collect.py`, `market_data/collector.py`
- NEW: `execution/latency.py`
- MODIFY: Parquet schema (if needed)

**Validation**:
- ✅ Feed latency reasonable (50-500ms)
- ✅ No negative latencies
- ✅ Smooth interpolation
- ✅ Flag outliers/gaps

**Data**: Depends on collection setup.

### Step 3: Derived Latency Mode (1-2 days)

**Goal**: Add `latency_mode="derived"` using feed latency.

**Tasks**:
1. Enhance `ExecutionAssumptions`:
   ```python
   latency_mode: str = "instant"
   latency_ms: int = 0
   entry_multiplier: float = 1.0
   response_multiplier: float = 1.0
   ```

2. Integrate `LatencyTracker` in `ShadowEngine`:
   ```python
   def update_quotes(self, ...):
       if latency_mode == "derived":
           feed_lat = latency_tracker.get_at(timestamp)
           active_at = timestamp + int(feed_lat * entry_mul)
   ```

3. Pass latency tracker from `replay.py`

4. Add tests: `test_latency_models.py`

5. Run experiment: Compare instant vs fixed vs derived

**Files**:
- MODIFY: `execution/shadow_engine.py`
- MODIFY: `backtest/replay.py`
- NEW: `tests/test_latency_models.py`

**Validation**:
- ✅ instant: fills immediately
- ✅ fixed: constant delay
- ✅ derived: varies with feed latency
- ✅ Derived latency > 0 reduces fills vs instant

**Mathematical Model**:
```
order_entry_latency = feed_latency * entry_multiplier
active_at = placed_at + order_entry_latency
```

**Data**: Requires feed latency from Step 2.

### Step 4: GTX Enforcement (0.5 days)

**Goal**: Add `enforce_gtx=True` to reject crossing orders.

**Tasks**:
1. Add `enforce_gtx: bool = False` to `ExecutionAssumptions`

2. Add GTX check in `update_quotes()`:
   ```python
   if enforce_gtx:
       if (order.side == "bid" and order.price >= market_ask) or \
          (order.side == "ask" and order.price <= market_bid):
           reject_order(reason="GTX_WOULD_CROSS")
           return  # Don't create order
   ```

3. Add tests: `test_gtx_enforcement.py`

**Files**:
- MODIFY: `execution/shadow_engine.py`
- NEW: `tests/test_gtx_enforcement.py`

**Validation**:
- ✅ Crossing order rejected when gtx=True
- ✅ Non-crossing order accepted
- ✅ gtx=False allows crossing (backwards compatible)

**Data**: No new data needed.

### Step 5: Integration and Experiments (2-3 days)

**Goal**: Run full experiment matrix, validate, document.

**Tasks**:
1. Create `scripts/run_execution_experiments.py`:
   - Run Experiments A-G (from section 16)
   - Collect metrics
   - Generate comparison table

2. Enhance `scripts/run_backtest.py`:
   - Add `--queue-model`
   - Add `--latency-mode`
   - Add `--enforce-gtx`
   - Add `--queue-power`
   - Add `--entry-multiplier`

3. Document results:
   - Execution model comparison table
   - Fill rate by model
   - PnL impact (descriptive, not prescriptive)
   - Sensitivity to parameters

4. Update documentation:
   - `docs/research/EXECUTION_MODELS.md` - User guide
   - `README` - Mention execution realism

**Files**:
- NEW: `scripts/run_execution_experiments.py`
- MODIFY: `scripts/run_backtest.py`
- NEW: `docs/research/EXECUTION_MODELS.md`

**Validation**:
- ✅ All experiments run successfully
- ✅ Results make intuitive sense (more conservative → fewer fills)
- ✅ Backwards compatible (old configs work)
- ✅ Documentation complete

### Step 6: Calibration Framework (Optional, 1-2 days)

**Goal**: Add tools for calibrating queue_power if live data available.

**Tasks**:
1. Create `scripts/calibrate_queue_model.py`:
   - Compare backtest fills to live fills
   - Search for `n` that minimizes difference
   - Output calibrated `queue_power`

2. Document calibration procedure in `docs/research/`

**Files**:
- NEW: `scripts/calibrate_queue_model.py`
- UPDATE: `docs/research/EXECUTION_MODELS.md`

**Validation**:
- ✅ Calibration converges
- ✅ Calibrated model matches live fills (if data available)

**Data**: Requires live fill data (future).

---

## IMPLEMENTATION PLAN

### Pre-Implementation Checklist ✅

Before starting any implementation:

- [x] Current execution model documented
- [x] HFTENGINE reference analyzed
- [x] Queue model mathematics understood
- [x] Latency model design complete
- [x] Test plan written
- [x] Files to modify identified
- [x] Implementation order prioritized
- [x] Risks documented
- [x] Backwards compatibility considered
- [ ] Team review of this design document **← DO THIS NEXT**

### Implementation Phases

**Phase A: Queue Model (Week 1)**
1. Day 1-2: Implement probabilistic queue (`execution/queue.py`, modify `shadow_engine.py`)
2. Day 3: Write tests (`test_queue_models.py`)
3. Day 4: Run experiments (shadow vs linear vs probabilistic)
4. Day 5: Document results, review

**Phase B: Latency Model (Week 2)**
1. Day 1: Verify/add feed latency measurement
2. Day 2-3: Implement derived latency mode (`execution/latency.py`, modify `shadow_engine.py`)
3. Day 4: Write tests (`test_latency_models.py`)
4. Day 5: Run experiments (instant vs fixed vs derived)

**Phase C: Integration (Week 3)**
1. Day 1: Add GTX enforcement (`test_gtx_enforcement.py`)
2. Day 2-3: Create execution experiment runner
3. Day 4: Run full experiment matrix (Exp A-G)
4. Day 5: Document, create comparison table

**Phase D: Polish (Week 4)**
1. Day 1-2: Enhance CLI, add user documentation
2. Day 3: Calibration framework (if applicable)
3. Day 4-5: Final validation, team review

### Success Criteria

**Must Have** (MVP):
- ✅ Probabilistic queue model working
- ✅ Backwards compatible (old experiments unchanged)
- ✅ Tests pass
- ✅ At least Experiments A, B, E run successfully
- ✅ Results documented

**Should Have**:
- ✅ Derived latency mode working
- ✅ Full experiment matrix (A-G)
- ✅ CLI enhancements
- ✅ User documentation

**Nice to Have**:
- ⭐ GTX enforcement
- ⭐ Response latency modeling
- ⭐ Calibration framework
- ⭐ Sensitivity analysis automation

### DO NOT

During implementation:
- ❌ Modify HFTENGINE reference
- ❌ Break existing experiments
- ❌ Add RL/AI features
- ❌ Build dashboard UI
- ❌ Optimize for PnL
- ❌ Change strategy behavior
- ❌ Modify C++ engine
- ❌ Rewrite everything from scratch

### STOP POINT

**This document is COMPLETE.**

**Next step**: Team review of this design.

**After approval**: Begin Phase A implementation following the exact plan above.

---

**End of Design Document**  
**Status**: Ready for Review  
**Estimated Implementation Time**: 3-4 weeks  
**Risk Level**: Medium (mitigated by incremental approach)  
**Value**: High (execution realism critical for strategy validation)
