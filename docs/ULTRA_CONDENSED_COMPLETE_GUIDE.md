# LOB-X: ULTRA-CONDENSED COMPLETE MASTERY GUIDE
**Read this entire document. Every section. Then ask me to expand ANY topic you want deeper understanding of.**

---

# 🎯 PROJECT ARCHITECTURE

## High-Level Flow
```
User Request → FastAPI → pybind11 → C++ Matching Engine → Trades → WebSocket Broadcast
```

## Three Layers
1. **C++ Infrastructure (backend/cpp/)**: Matching engine, price-time priority, O(log n) operations
2. **Python Application (backend/python/lobx/)**: Analytics, models, strategies, market data
3. **React Frontend**: Real-time visualizations, order management UI

---

# 📘 PART 1: C++ MATCHING ENGINE

## Core Concept: Price-Time Priority

**Rule**: Orders matched by (1) best price, (2) earliest time within same price

**Example**:
```
ASK: 101.00 [100 shares - Order A, 200 shares - Order B]
BID: 100.00 [150 shares]

Market buy 250 shares arrives:
  1. Fills 100 from Order A @ 101.00 (first in queue)
  2. Fills 150 from Order B @ 101.00 (remaining at that level)
  3. 0 shares unfilled (or would move to next ask level)
```

## Data Structures (order_book.hpp)

### PriceLevel (price_level.hpp)
```cpp
class PriceLevel {
    std::deque<Order> orders_;  // FIFO queue
    Quantity total_qty_;        // Sum of all qty_remaining
};
```
- `deque`: O(1) push_back, O(1) pop_front (FIFO)
- Why not `queue`? Need to iterate for cancel-by-ID

### OrderBook
```cpp
class OrderBook {
    // Bids: highest price first (reverse/descending)
    std::map<Price, PriceLevel, std::greater<Price>> bids_;
    
    // Asks: lowest price first (default/ascending)
    std::map<Price, PriceLevel> asks_;
    
    // Order ID → Price lookup for O(log n) cancel
    std::unordered_map<OrderId, Price> order_index_;
};
```

**Why `std::map`?**
- Keeps prices sorted automatically
- O(log n) insert/remove
- begin() always gives best price (highest bid / lowest ask)

**Why `std::greater<Price>` for bids?**
- Default map sorts ascending (low→high)
- Bids need descending (high→low)
- `std::greater` reverses the order

## Matching Algorithm (order_book.cpp)

```cpp
template <typename PassiveMap>
std::vector<Trade> OrderBook::match(Order& aggressor, PassiveMap& passive_side) {
    std::vector<Trade> trades;
    
    while (aggressor.qty_remaining > 0 && !passive_side.empty()) {
        auto& [passive_price, level] = *passive_side.begin();  // Best price
        
        // Price check
        if (aggressor.is_buy() && aggressor.price < passive_price) break;
        if (aggressor.is_sell() && aggressor.price > passive_price) break;
        
        // Fill against level (FIFO)
        while (aggressor.qty_remaining > 0 && !level.empty()) {
            auto& passive = level.front().value().get();
            Quantity fill_qty = std::min(aggressor.qty_remaining, passive.qty_remaining);
            
            trades.emplace_back(aggressor.id, passive.id, passive_price, fill_qty);
            
            aggressor.qty_remaining -= fill_qty;
            level.reduce_front(fill_qty);
        }
        
        if (level.empty()) passive_side.erase(passive_side.begin());
    }
    return trades;
}
```

**Key Points**:
1. Passive order's price wins (price-maker, not taker)
2. Loop through levels until aggressor filled or no more matches
3. FIFO within each level (front() of deque)
4. Remove empty levels from map

## Cancel Logic
```cpp
bool OrderBook::cancel(OrderId id) {
    // O(1) lookup in order_index
    auto it = order_index_.find(id);
    if (it == order_index_.end()) return false;
    
    Price price = it->second;
    order_index_.erase(it);
    
    // O(log n) find price level
    // O(n) scan within level (but n typically small)
    // Remove order from deque
}
```

## Complexity Analysis
- **Submit order**: O(log m + k) where m=price levels, k=fills
- **Cancel order**: O(log m + n) where n=orders at that price
- **Query best bid/ask**: O(1) (just begin() of map)

---

# 📗 PART 2: PYBIND11 BRIDGE

## What is pybind11?

**Problem**: C++ is fast, Python is flexible. How to call C++ from Python with zero overhead?

**Solution**: pybind11 generates Python bindings at compile-time.

## Example (py_bindings.cpp)

```cpp
PYBIND11_MODULE(lobx_cpp, m) {
    py::class_<Order>(m, "Order")
        .def(py::init<OrderId, Price, Quantity, bool, TimeInForce>())
        .def_readwrite("id", &Order::id)
        .def_readonly("is_buy", [](const Order& o){ return o.is_buy(); });
    
    py::class_<OrderBook>(m, "OrderBook")
        .def(py::init<>())
        .def("add_limit_order", &OrderBook::add_limit_order)
        .def("cancel", &OrderBook::cancel)
        .def("best_bid", &OrderBook::best_bid);
}
```

## From Python:
```python
import lobx_cpp

book = lobx_cpp.OrderBook()
order = lobx_cpp.Order(id=1, price=10000, qty=100, is_buy=True)
trades = book.add_limit_order(order)  # Calls C++ directly!

print(book.best_bid())  # Returns Option[int]
```

**Performance**: Same as calling C++ (no serialization, no copies, in-process)

---

# 📙 PART 3: PYTHON ANALYTICS

## Microprice (analytics/microprice.py)

**Concept**: Better mid-price estimate using volume weighting

**Formula**:
```
microprice = (ask * bid_qty + bid * ask_qty) / (bid_qty + ask_qty)
```

**Intuition**: If there's MORE volume on bid side, price likely to move UP (toward ask)

**Example**:
```
Bid: 100.00 @ 500 shares
Ask: 100.02 @ 100 shares

Simple mid = (100.00 + 100.02) / 2 = 100.01

Microprice = (100.02 * 500 + 100.00 * 100) / (500 + 100)
           = (50010 + 10000) / 600
           = 100.0167  ← Closer to ask (predicts upward pressure)
```

## Order Book Imbalance (analytics/imbalance.py)

**Formula**:
```
OBI = (bid_qty - ask_qty) / (bid_qty + ask_qty)
```

**Range**: [-1, +1]
- +1 = all bid volume (strong buy pressure)
- -1 = all ask volume (strong sell pressure)
-  0 = balanced

**Use Case**: Predict short-term price direction

## Volatility (analytics/volatility.py)

**EWMA (Exponentially Weighted Moving Average)**:
```
variance_t = λ * variance_{t-1} + (1-λ) * r_t²
volatility_t = sqrt(variance_t)

where r_t = log(mid_t / mid_{t-1})  # Log return
```

**Parameter λ (decay)**:
- λ = 0.94: RiskMetrics standard (daily data)
- λ = 0.98: High-frequency (more smoothing)
- Higher λ = slower adaptation, smoother
- Lower λ = faster adaptation, noisier

**Code**:
```python
class EWMAVolatilityEstimator:
    def update(self, mid_price: float) -> float:
        if self._last_mid is None:
            self._last_mid = mid_price
            return 0.0
        
        log_return = math.log(mid_price / self._last_mid)
        self._variance = (
            self.decay * self._variance +
            (1.0 - self.decay) * log_return ** 2
        )
        self._last_mid = mid_price
        return math.sqrt(self._variance)
```

---

# 📕 PART 4: AVELLANEDA-STOIKOV MODEL (THE CROWN JEWEL)

## Problem Statement

You're a **market maker**:
- Goal: Buy low, sell high, capture spread repeatedly
- Risk: Accumulate inventory (if you buy 1000 shares, you're exposed to price drops)

**Trade-off**:
- Wide spread → capture more profit per trade, but fewer fills
- Narrow spread → more fills, but less profit per trade
- Inventory → adjust quotes to reduce risk

## The Model

### Reservation Price
**Formula**:
```
r_t = s_t - q_t * γ * σ² * (T - t)
```

**Variables**:
- `s_t`: Current mid-price
- `q_t`: Inventory (+ = long, - = short)
- `γ`: Risk aversion parameter (higher = more aggressive hedging)
- `σ`: Volatility
- `T - t`: Time remaining in session

**Intuition**:
- If `q > 0` (long), `r < s` (reservation below mid)
  → You're willing to sell cheaper / buy at lower price (encourage selling)
- If `q < 0` (short), `r > s` (reservation above mid)
  → You're willing to buy higher / sell at higher price (encourage buying)

### Optimal Spread
**Formula**:
```
δ* = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/κ)
```

**Variables**:
- `κ` (kappa): Fill rate parameter (higher = more fills expected)

**Terms**:
1. `γ * σ² * (T - t)`: Risk term (widen spread as vol ↑ or time ↑)
2. `(2/γ) * ln(1 + γ/κ)`: Liquidity term (narrow spread if fills are frequent)

### Quotes
```
bid = r - δ*/2
ask = r + δ*/2
```

## Derivation (Math Deep Dive)

The AS model comes from solving a **stochastic optimal control problem**.

### Setup

**State**: `(s, q, t)` where
- `s`: Mid-price (follows Brownian motion)
- `q`: Inventory
- `t`: Time

**Dynamics**:
```
ds = σ dW  (mid-price is a Brownian motion with volatility σ)
dq = dN^bid - dN^ask  (inventory changes with fills)
```

**Utility**: Exponential CARA (Constant Absolute Risk Aversion)
```
U(x) = -exp(-γ * x)
```

**Objective**: Maximize expected utility of terminal wealth
```
E[U(X_T)] = E[-exp(-γ * (X_T))]
where X_T = cash + q_T * s_T (terminal wealth)
```

### Hamilton-Jacobi-Bellman (HJB) Equation

The value function `H(s, q, t)` satisfies:
```
∂H/∂t + (σ²/2) * ∂²H/∂s² + λ^bid * (H(s, q+1, t) - H) + λ^ask * (H(s, q-1, t) - H) = 0
```

Where `λ^bid`, `λ^ask` are arrival rates of fills (depends on quote placement).

**Solving** (using ansatz `H = -exp(-γ(qs + θ(t, q)))`):
→ Reservation price: `r = s - q * γ * σ² * (T - t)`
→ Optimal spread: `δ = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/κ)`

## Calibration

**Parameters to tune**:
1. **γ (gamma)**: Risk aversion
   - Start: 0.1
   - Higher γ → tighter quotes when inventory builds
   - Calibrate by: backtest different values, measure Sharpe ratio

2. **κ (kappa)**: Fill rate
   - Estimate from historical data: κ = # fills per unit time
   - Or: fit from Hawkes process (next section)

3. **σ (sigma)**: Volatility
   - Use EWMA estimator (from analytics/volatility.py)
   - Or: realized volatility from historical mid-prices

## Implementation (models/avellaneda/market_maker.py)

```python
class AvellanedaStoikovMM(BaseTrader):
    def step(self) -> list[Trade]:
        time_remaining = (self._T - self._t) / self._T
        
        mid = self.book.mid_price()
        r = reservation_price(mid, self._inventory, self._gamma, self._sigma, time_remaining)
        hs = half_spread(self._gamma, self._sigma, time_remaining, self._kappa)
        
        bid_px = round(r - hs)
        ask_px = round(r + hs)
        
        # Cancel old quotes
        if self._bid_id: self.book.cancel(self._bid_id)
        if self._ask_id: self.book.cancel(self._ask_id)
        
        # Submit new quotes
        trades = []
        trades += self.book.add_limit_order(Order(bid_px, qty, is_buy=True))
        trades += self.book.add_limit_order(Order(ask_px, qty, is_buy=False))
        
        # Update inventory
        for t in trades:
            if t.buy_id == self._bid_id: self._inventory += t.qty
            if t.sell_id == self._ask_id: self._inventory -= t.qty
        
        return trades
```

---

# 📗 PART 5: ALMGREN-CHRISS MODEL

## Problem Statement

You have a **LARGE order**: Sell 1,000,000 shares over 1 hour.

**Challenges**:
1. **Market impact**: If you sell all at once, price drops (you move the market)
2. **Timing risk**: If you sell slowly, price might move against you
3. **Trade-off**: Aggressive execution (less timing risk) vs passive (less impact)

## The Model

### Objective
Minimize: `E[cost] + λ * Var[cost]`
- Expected cost (market impact + slippage)
- Variance of cost (risk from price uncertainty)
- λ: Risk aversion

### Trajectory
**Optimal inventory path** (amount remaining at each time):
```
x(t) = X * sinh(κ(T - t)) / sinh(κT)

where κ = sqrt(λσ²/η)
```

**Variables**:
- `X`: Total shares to sell
- `T`: Time horizon
- `σ`: Volatility
- `η`: Temporary impact coefficient
- `λ`: Risk aversion

### Trade Schedule
```
trades[i] = x(t_i) - x(t_{i+1})  (amount to sell in period i)
```

**Shape**:
- High risk aversion (large λ) → trade more aggressively early
- Low risk aversion (small λ) → trade more evenly

## Implementation (models/almgren/trajectory.py)

```python
def optimal_trajectory(total_shares, T, sigma, eta, gamma, lambda_):
    kappa = math.sqrt(lambda_ * sigma**2 / eta)
    t_vals = np.arange(T + 1)
    trajectory = total_shares * np.sinh(kappa * (T - t_vals)) / np.sinh(kappa * T)
    return trajectory

def trade_list(trajectory):
    return -np.diff(trajectory)  # Differences = trades per period
```

## Execution (models/almgren/executor.py)

```python
class AlmgrenChrissExecutor:
    def __init__(self, total_shares, T, sigma, eta, gamma, lambda_):
        traj = optimal_trajectory(total_shares, T, sigma, eta, gamma, lambda_)
        self._schedule = [round(q) for q in trade_list(traj)]
        self._tick = 0
    
    def step(self):
        if self._tick >= len(self._schedule):
            return []
        
        child_qty = self._schedule[self._tick]
        self._tick += 1
        
        # Submit limit order for this chunk (use AS to execute each slice)
        return self.book.add_limit_order(Order(...))
```

---

# 📘 PART 6: HAWKES PROCESS

## Problem

**Observation**: Order arrivals cluster in time
- One trade triggers more trades (herding, information cascade)
- News event → burst of orders

**Question**: How to model this self-exciting behavior?

## The Model

**Intensity (arrival rate)**:
```
λ(t) = μ + Σ_{t_i < t} α * exp(-β * (t - t_i))
```

**Components**:
1. `μ`: Baseline intensity (background arrival rate)
2. `α`: Excitation (jump size when event occurs)
3. `β`: Decay rate (how fast excitement fades)

**Stability**: Requires `α < β` (otherwise explodes)

## Intuition

```
Time:   0    1    2    3    4    5
Events: *         *              *
        
t=0: Event occurs → λ jumps by α
t=1: λ decays by exp(-β*1)
t=2: Another event → λ jumps again + residual from t=0
t=3-4: Decay continues
t=5: Another event
```

**Intensity curve**:
```
λ(t)
 |    ╱╲         ╱╲               ╱╲
 |   ╱  ╲       ╱  ╲             ╱  ╲
 |  ╱    ╲_____╱    ╲___________╱    ╲___
 | ╱                                      ╲___
 |╱________________________________________╲___
  0    1    2    3    4    5    6    7    8
```

## Calibration (MLE)

**Log-likelihood**:
```
LL = -μ*T + Σ log(λ(t_i)) - (α/β) * Σ (1 - exp(-β*(T - t_i)))
```

**Algorithm**:
1. Collect event times `[t_1, t_2, ..., t_n]`
2. Maximize LL over `(μ, α, β)` using scipy.optimize

**Code** (models/hawkes/calibration.py):
```python
def log_likelihood(params, events, T):
    mu, alpha, beta = params
    ll = -mu * T
    
    r = 0.0
    for i in range(len(events)):
        if i > 0:
            r = np.exp(-beta * (events[i] - events[i-1])) * (1 + r)
        lam_i = mu + alpha * r
        ll += np.log(lam_i)
    
    integral = np.sum(1 - np.exp(-beta * (T - events)))
    ll -= (alpha / beta) * integral
    return -ll

def calibrate(events, T):
    result = minimize(log_likelihood, x0=[1, 0.5, 1], args=(events, T))
    return {"mu": result.x[0], "alpha": result.x[1], "beta": result.x[2]}
```

## Use in Trading

**Applications**:
1. Estimate `κ` parameter for AS model (κ ≈ λ)
2. Predict next event time (for optimal quote placement)
3. Detect regime changes (sudden λ spike = news event)

---

# 📙 PART 7: MARKET DATA PIPELINE

## Architecture

```
Binance Exchange
    ↓ WebSocket (2 streams)
BinanceWSClient
    ↓ Parse JSON
Collector (orchestrator)
    ↓ Route by stream type
    ├─→ @depth → DepthStreamManager
    │            ↓ Sync + Apply
    │         LocalOrderBook (Python)
    │            ↓ Convert float→ticks
    │         EngineMirrorBridge
    │            ↓ Insert synthetic orders
    │         OrderBook (C++)
    │            ↓ Write rows
    │         Parquet (storage)
    │
    └─→ @trade → TradeTick parser
                 ↓ Write rows
              Parquet (storage)
```

## Depth Synchronization (depth_stream_manager.py)

**Problem**: Binance sends incremental updates (diffs), not full snapshots.

**Solution**:
1. Buffer diff events until first REST snapshot arrives
2. Apply snapshot to LocalOrderBook
3. Replay buffered diffs (verify sequence numbers match)
4. From then on, apply diffs in real-time

**Sequence Check**:
```python
if event["U"] != self._prev_final_uid + 1:
    # Gap detected → resync
    self.mark_desynced("sequence gap")
```

## Parquet Storage

**Why Parquet?**
- Columnar format (fast filtering by timestamp)
- Compression (~10x smaller than CSV)
- Schema enforcement
- Direct Pandas/NumPy integration

**Schemas**:
```python
DEPTH_SCHEMA = pa.schema([
    ("event_time", pa.int64()),
    ("symbol", pa.string()),
    ("side", pa.string()),  # "bid" or "ask"
    ("price", pa.float64()),
    ("qty", pa.float64()),
    ("first_update_id", pa.int64()),
    ("final_update_id", pa.int64()),
])

TRADE_SCHEMA = pa.schema([
    ("event_time", pa.int64()),
    ("trade_id", pa.int64()),
    ("price", pa.float64()),
    ("qty", pa.float64()),
    ("is_buyer_maker", pa.bool_()),
    ("symbol", pa.string()),
])
```

## Engine Mirror (engine_mirror.py)

**Goal**: Keep C++ OrderBook in sync with Binance

**Challenge**: Binance gives price levels (aggregate qty), C++ needs individual orders

**Solution**: Synthetic orders
- Each price level = 1 synthetic order
- Order ID = deterministic hash of (side, price)
- Update = cancel old synthetic order + insert new one

**ID Encoding**:
```python
def _synthetic_id(side, price):
    scaled = int(round(price * 1e8)) & 0x7FFFFFFFFFFFFFFF  # 63 bits
    if side == "ask":
        return scaled | (1 << 63)  # Set bit 63 for ask
    return scaled
```

**Price Conversion**:
```python
TICKS_PER_UNIT = 100  # $0.01 resolution
price_ticks = round(price * TICKS_PER_UNIT)
```

---

# 📕 PART 8: BACKTESTING

## Replay Engine (backtest/replay.py)

**Input**: Parquet files (depth + trades from historical capture)

**Process**:
1. Iterate through events in timestamp order
2. Apply depth updates to LocalOrderBook
3. Strategy decides quotes based on current book state
4. ShadowQuoteEngine tracks hypothetical fills (no C++ needed)
5. Record P&L, fills, inventory over time

**Key Assumption**: Latency simulation
```python
quote_interval_ms = 100  # Can only update quotes every 100ms
```

## Shadow Engine (execution/shadow_engine.py)

**Purpose**: Simulate fills WITHOUT actually matching in C++ engine

**Logic**:
```python
def on_trade(self, tick: TradeTick):
    # Check if our quote would have been filled
    if tick.is_buyer_maker:  # Sell trade
        if self.bid_quote and tick.price <= self.bid_quote.price:
            # Our bid would have been hit
            self.fill_bid(tick.price, min(tick.qty, self.bid_quote.qty))
    else:  # Buy trade
        if self.ask_quote and tick.price >= self.ask_quote.price:
            # Our ask would have been lifted
            self.fill_ask(tick.price, min(tick.qty, self.ask_quote.qty))
```

**Queue Position**: Account for orders ahead of us
```python
bid_ahead = book.bids.get(bid_price, 0.0)  # How much qty ahead of us?
# Adjust fill probability based on queue position
```

## Metrics

```python
summary = {
    "net_pnl": final_equity,
    "gross_pnl": inventory.total_pnl(mark_price),
    "realized_pnl": inventory.realized_pnl,
    "fees_paid": total_fees,
    "turnover": sum(price * qty for all fills),
    "fills": fill_count,
    "max_abs_position": max(abs(inventory)),
    "mean_abs_position": mean(abs(inventory)),
    "max_drawdown": max(peak - equity for all equity points),
    "mean_1s_markout": mean(signed_qty * (price_1s_later - fill_price)),
}
```

---

# 📗 PART 9: LIVE STRATEGY DEPLOYMENT

## Quote Loop (live/quote_loop.py)

```python
async def run_live_strategy(strategy, collector):
    while True:
        await asyncio.sleep(0.1)  # 100ms tick
        
        book = collector.depth_mgr.book
        mid = book.mid_price()
        if not mid:
            continue
        
        state = MarketState(
            timestamp=now_ms(),
            mid_price=mid[0],
            best_bid=book.best_bid()[0],
            best_ask=book.best_ask()[0],
            volatility=vol_estimator.current(),
            inventory=inventory_tracker.position,
        )
        
        quotes = strategy.on_tick(state)
        
        # TODO: Submit to real exchange via API
        # exchange_client.submit_order(quotes.bid)
        # exchange_client.submit_order(quotes.ask)
```

## Risk Management (risk/)

**Limits**:
```python
class MarketMakingRiskConfig:
    max_position: float = 1.0  # BTC
    max_loss: float = -1000.0  # USD
    max_drawdown: float = 500.0  # USD
```

**Halt Conditions**:
```python
if abs(position) > config.max_position:
    halt("Position limit breached")

if equity < config.max_loss:
    halt("Loss limit breached")
```

---

# 📘 PART 10: PERFORMANCE OPTIMIZATION

## C++ Optimizations

### 1. Container Choices
- `std::map`: O(log n) but cache-unfriendly (pointer chasing)
- Alternative: Flat sorted array (O(n) insert but better cache locality)
- Profile first before optimizing

### 2. Move Semantics
```cpp
void rest_order(Order order);  // Pass by value + move
order_index_[order.id] = price;
bids_[price].add(std::move(order));  // Avoid copy
```

### 3. constexpr
```cpp
constexpr Price TICK_SIZE = 100;  // Compile-time constant
```

### 4. Lock-Free (Advanced)
- Use `std::atomic` for order ID generation
- Lock-free queues for parallel order submission
- Requires deep understanding of memory ordering

## Python Optimizations

### 1. NumPy Vectorization
```python
# BAD (Python loop)
for i in range(len(prices)):
    returns[i] = log(prices[i] / prices[i-1])

# GOOD (NumPy)
returns = np.log(prices[1:] / prices[:-1])
```

### 2. Pandas groupby
```python
# Aggregate trades by minute
df.groupby(df.timestamp // 60000).agg({"qty": "sum", "price": "mean"})
```

### 3. Cython (if needed)
- Compile hot loops to C
- For analytics that are pure computation (no I/O)

---

# 🎓 NEXT STEPS

## To Become $50K+ Hire-Ready

### 1. Technical Depth
- [ ] Implement lock-free order book (C++ atomics)
- [ ] SIMD vectorization for analytics (AVX2)
- [ ] Custom memory allocator (pool allocator for orders)
- [ ] Sub-microsecond latency (kernel bypass, DPDK)

### 2. Quant Sophistication
- [ ] Derive AS model from HJB equation (by hand)
- [ ] Implement regime-switching AS (detect volatility regimes)
- [ ] Multi-level AS (quote at multiple price levels)
- [ ] Optimal execution with adverse selection

### 3. Research
- [ ] Implement a paper from scratch (e.g., Gueant-Lehalle-Fernandez-Tapia)
- [ ] Write blog post comparing AS vs empirical strategies
- [ ] Open-source a well-documented component

### 4. Production Quality
- [ ] Comprehensive unit tests (C++ Catch2, Python pytest)
- [ ] Benchmarking suite (latency percentiles, throughput)
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Monitoring (Prometheus + Grafana)

---

# 🚀 HOW TO USE THIS GUIDE

1. **Read this document completely** (you're doing it!)
2. **Ask me to EXPAND any section**: "Explain X in more detail"
3. **Code along**: Open the actual files, trace through with GDB/print statements
4. **Modify**: Change parameters, add features, break things and fix them
5. **Measure**: Benchmark, backtest, compare

**I'm here to answer EVERY question. Ask me to:**
- Derive any formula step-by-step
- Explain any code line-by-line
- Implement a variant (e.g., "add FOK order type support")
- Debug any issue
- Optimize any bottleneck

**Let's master this system together. What do you want to dive into first?**
