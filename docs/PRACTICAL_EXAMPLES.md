# PRACTICAL EXAMPLES - HANDS-ON CODE WALKTHROUGHS

**This document shows you EXACTLY how to run, modify, and understand every component.**

---

## 🔧 SETUP - First Time

### 1. Build C++ Engine

```powershell
cd backend/cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
```

**What this does**:
- Compiles C++ code to `lobx_core.lib` (static library)
- Builds `lobx_cpp.pyd` (Python extension) via pybind11
- Creates `lobx_tests.exe` (unit tests)

### 2. Run C++ Tests

```powershell
cd backend/cpp/build
ctest --output-on-failure
```

**Expected output**:
```
Test project C:/Users/archa/lob-x/backend/cpp/build
    Start 1: lobx_tests
1/1 Test #1: lobx_tests .......................   Passed    0.03 sec

100% tests passed, 0 tests failed out of 1
```

### 3. Install Python Package

```powershell
cd backend/python
pip install -e ".[dev]"
```

**What this installs**:
- FastAPI, websockets, numpy, pandas, scipy
- pytest, ruff (linter), mypy (type checker)
- Your lobx package in editable mode

### 4. Run Python Tests

```powershell
cd backend/python
pytest tests/ -v
```

---

## 📚 EXAMPLE 1: Using C++ Engine from Python

### A. Simple Order Matching

Create `test_simple.py`:

```python
import lobx_cpp

# Create a book
book = lobx_cpp.OrderBook()

# Submit orders
print("=== Submitting orders ===")
trades = book.add_limit_order(lobx_cpp.Order(1, 10000, 100, is_buy=True))  # Bid 100.00 @ 100
print(f"Trades from bid: {trades}")

trades = book.add_limit_order(lobx_cpp.Order(2, 10002, 50, is_buy=False))  # Ask 100.02 @ 50
print(f"Trades from ask: {trades}")

# Check book state
print(f"\nBest bid: {book.best_bid()}")  # 10000
print(f"Best ask: {book.best_ask()}")    # 10002
print(f"Mid: {book.mid_price()}")        # 10001.0
print(f"Spread: {book.spread()}")        # 2

# Submit crossing order (will match!)
print("\n=== Crossing order ===")
trades = book.add_limit_order(lobx_cpp.Order(3, 10000, 30, is_buy=False))  # Sell @ 100.00
print(f"Trades generated: {len(trades)}")
for t in trades:
    print(f"  Trade: buy_id={t.buy_id}, sell_id={t.sell_id}, price={t.price}, qty={t.qty}")

# Check final state
print(f"\nFinal best bid: {book.best_bid()}")  # Still 10000
print(f"Final bid qty: {book.best_bid_qty()}")  # 70 remaining (100 - 30)
```

**Run it**:
```powershell
python test_simple.py
```

**Expected output**:
```
=== Submitting orders ===
Trades from bid: []
Trades from ask: []

Best bid: 10000
Best ask: 10002
Mid: 10001.0
Spread: 2

=== Crossing order ===
Trades generated: 1
  Trade: buy_id=1, sell_id=3, price=10000, qty=30

Final best bid: 10000
Final bid qty: 70
```

---

### B. Price-Time Priority Demo

```python
import lobx_cpp

book = lobx_cpp.OrderBook()

# Add 3 bids at SAME price (different times)
print("Adding 3 bids at same price...")
book.add_limit_order(lobx_cpp.Order(100, 10000, 10, True))  # First
book.add_limit_order(lobx_cpp.Order(101, 10000, 20, True))  # Second
book.add_limit_order(lobx_cpp.Order(102, 10000, 30, True))  # Third

# Sell 15 shares (should fill Order 100 completely, then part of 101)
print("\nSelling 15 shares...")
trades = book.add_limit_order(lobx_cpp.Order(200, 10000, 15, False))

print(f"Number of trades: {len(trades)}")
for t in trades:
    print(f"  Filled order {t.buy_id} with {t.qty} shares")

# Expected: Order 100 fills (10 shares), Order 101 partially fills (5 shares)
```

---

### C. Cancel Order Demo

```python
import lobx_cpp

book = lobx_cpp.OrderBook()

# Submit orders
book.add_limit_order(lobx_cpp.Order(1, 10000, 100, True))
book.add_limit_order(lobx_cpp.Order(2, 10001, 200, True))
book.add_limit_order(lobx_cpp.Order(3, 10002, 50, False))

print(f"Before cancel: {book.bid_levels()} bid levels")
print(f"Best bid: {book.best_bid()}")

# Cancel Order 2
success = book.cancel(2)
print(f"\nCancel successful: {success}")
print(f"After cancel: {book.bid_levels()} bid levels")
print(f"Best bid: {book.best_bid()}")  # Should be 10000 now

# Try cancelling same order again
success = book.cancel(2)
print(f"\nDouble cancel: {success}")  # Should be False
```

---

## 📊 EXAMPLE 2: Analytics

### A. Microprice Calculation

```python
import lobx_cpp
from lobx.analytics.microprice import compute_microprice, MicropriceMonitor

book = lobx_cpp.OrderBook()

# Scenario: Heavy bid side, light ask side
book.add_limit_order(lobx_cpp.Order(1, 10000, 1000, True))  # Bid 1000 shares
book.add_limit_order(lobx_cpp.Order(2, 10002, 100, False))  # Ask 100 shares

# Simple mid
simple_mid = book.mid_price()
print(f"Simple mid: {simple_mid}")  # 10001.0

# Microprice (weighted by opposite side volume)
micro = compute_microprice(book)
print(f"Microprice: {micro}")  # Closer to 10002 (predicts upward move)

# Difference
print(f"Drift: {micro - simple_mid}")  # Positive = upward pressure
```

---

### B. Order Book Imbalance

```python
from lobx.analytics.imbalance import compute_obi
import lobx_cpp

book = lobx_cpp.OrderBook()

# Balanced book
book.add_limit_order(lobx_cpp.Order(1, 10000, 500, True))
book.add_limit_order(lobx_cpp.Order(2, 10002, 500, False))
print(f"Balanced OBI: {compute_obi(book)}")  # ~0

# Heavy bid side
book.add_limit_order(lobx_cpp.Order(3, 9999, 1000, True))
print(f"Bid-heavy OBI: {compute_obi(book)}")  # Positive

# Heavy ask side
book.add_limit_order(lobx_cpp.Order(4, 10003, 2000, False))
print(f"Ask-heavy OBI: {compute_obi(book)}")  # Negative
```

---

### C. Volatility Estimation

```python
from lobx.analytics.volatility import EWMAVolatilityEstimator

estimator = EWMAVolatilityEstimator(decay=0.98)

# Feed mid-prices
mid_prices = [100.0, 100.5, 99.8, 100.2, 101.0, 100.3]

for mid in mid_prices:
    vol = estimator.update(mid)
    print(f"Mid: {mid:6.2f}, Vol: {vol:.6f}")

# Final volatility
print(f"\nFinal volatility: {estimator.current():.6f}")
```

---

## 🎯 EXAMPLE 3: Avellaneda-Stoikov Strategy

### A. Manual Calculation

```python
from lobx.models.avellaneda.reservation_price import reservation_price
from lobx.models.avellaneda.optimal_spread import half_spread

# Parameters
mid = 100.0
inventory = 10.0  # Long 10 BTC
gamma = 0.1       # Risk aversion
sigma = 0.02      # Volatility (2%)
time_remaining = 0.5  # 50% of session left
kappa = 1.5       # Fill rate

# Calculate reservation price
r = reservation_price(mid, inventory, gamma, sigma, time_remaining)
print(f"Mid: {mid}")
print(f"Reservation (with inventory={inventory}): {r:.4f}")
print(f"Shift: {r - mid:.4f}")  # Should be NEGATIVE (skew down)

# Calculate optimal spread
hs = half_spread(gamma, sigma, time_remaining, kappa)
print(f"\nHalf-spread: {hs:.4f}")

# Final quotes
bid = r - hs
ask = r + hs
print(f"\nBid: {bid:.4f}")
print(f"Ask: {ask:.4f}")
print(f"Spread: {ask - bid:.4f}")
```

**Expected behavior**:
- If `inventory > 0` (long), `r < mid` (reservation below mid)
- Bid and ask are BOTH shifted down (encourage selling to reduce inventory)

---

### B. Simulated Market Making

```python
import lobx_cpp
from lobx.models.avellaneda.market_maker import AvellanedaStoikovMM

# Create book with initial liquidity
book = lobx_cpp.OrderBook()
book.add_limit_order(lobx_cpp.Order(1, 10000, 100, True))  # Someone's bid
book.add_limit_order(lobx_cpp.Order(2, 10010, 100, False))  # Someone's ask

# Create AS market maker
mm = AvellanedaStoikovMM(
    book=book,
    trader_id_start=1000,
    gamma=0.1,
    sigma=1.0,
    kappa=1.5,
    session_ticks=100,
    qty=5
)

# Run 10 ticks
for tick in range(10):
    trades = mm.step()
    if trades:
        print(f"Tick {tick}: {len(trades)} trades")
    
    # Check MM's quotes
    bid = book.best_bid()
    ask = book.best_ask()
    if bid and ask:
        print(f"  Book: {bid} / {ask}, Spread: {ask - bid}")

print(f"\nFinal inventory: {mm._inventory}")
```

---

## 🔬 EXAMPLE 4: Hawkes Process

### A. Simulate Events

```python
from lobx.models.hawkes.intensity import HawkesIntensity
import numpy as np

# Parameters
mu = 0.5     # Baseline: 0.5 events/second
alpha = 0.3  # Each event adds 0.3 to intensity
beta = 1.0   # Decay rate

hawkes = HawkesIntensity(mu, alpha, beta)

# Simulate 10 seconds
events = hawkes.simulate(T=10.0, seed=42)
print(f"Generated {len(events)} events")
print(f"First 10 events: {events[:10]}")

# Plot (if matplotlib available)
try:
    import matplotlib.pyplot as plt
    plt.hist(events, bins=20)
    plt.xlabel("Time (s)")
    plt.ylabel("Event count")
    plt.title("Hawkes Process Events")
    plt.show()
except ImportError:
    print("Install matplotlib to visualize")
```

---

### B. Calibrate from Real Data

```python
from lobx.models.hawkes.calibration import calibrate
import numpy as np

# Generate synthetic data
true_mu, true_alpha, true_beta = 1.0, 0.5, 2.0
events = np.cumsum(np.random.exponential(1/true_mu, 100))
events = events[events < 100]  # Keep events within [0, 100]

print(f"True params: μ={true_mu}, α={true_alpha}, β={true_beta}")

# Calibrate
result = calibrate(events, T=100.0, n_starts=5)
print(f"Estimated params: μ={result['mu']:.3f}, α={result['alpha']:.3f}, β={result['beta']:.3f}")
```

---

## 📈 EXAMPLE 5: Run Full Simulation

```powershell
python scripts/run_simulation.py --ticks 1000 --mid 10000 --seed 42
```

**What happens**:
1. Creates C++ OrderBook
2. Registers 5 trader types:
   - RandomTrader (noise)
   - MomentumTrader (trend following)
   - MeanReversionTrader (contrarian)
   - NaiveMarketMaker (fixed spread)
   - AvellanedaStoikovMM (optimal MM)
3. Runs 1000 ticks
4. Prints stats

**Output**:
```
Ticks:        1000
Total trades: 456
Total volume: 12345
Final book:   bid=10005  ask=10007
```

---

## 📊 EXAMPLE 6: Collect Live Market Data

### Start Collector (Terminal 1)

```powershell
python scripts/run_collector.py --no-engine-mirror --log-level INFO
```

**What you'll see**:
```
2026-08-18 12:00:00.123  INFO  lobx.market_data.collector  Collector starting for BTCUSDT
2026-08-18 12:00:00.456  INFO  lobx.market_data.binance_ws_client  Connected to wss://...
2026-08-18 12:00:01.789  INFO  lobx.market_data.depth_stream_manager  [BTCUSDT] Fetching REST snapshot...
2026-08-18 12:00:02.456  INFO  lobx.market_data.depth_stream_manager  [BTCUSDT] Fully synced.
2026-08-18 12:00:30.123  INFO  lobx.market_data.collector  [BTCUSDT] heartbeat | bid=67543.21×1.234  ask=67543.22×0.567  spread=0.01  levels=1000/1000  synced=True
```

### Watch Live (Terminal 2)

```powershell
python scripts/watch_live.py
```

**Output (updates every second)**:
```
=== BTCUSDT Live Book ===
Bid: 67543.21 (1.234 BTC)
Ask: 67543.22 (0.567 BTC)
Mid: 67543.215
Spread: 0.01
OBI: 0.37 (bid pressure)

Last trade: 67543.22 @ 0.05 BTC (buyer maker)
```

### Stop Collector

Press `Ctrl+C` in Terminal 1. Files are automatically flushed to `data/raw/`.

---

## 🧪 EXAMPLE 7: Backtest a Strategy

### Run Backtest

```powershell
python scripts/run_backtest.py --date 2026-08-18 --strategy as --scenario baseline
```

**What it does**:
1. Loads `data/raw/depth_2026-08-18.parquet` + trades
2. Replays through HistoricalReplay engine
3. Strategy (AS) quotes every 100ms based on current book
4. ShadowEngine simulates fills
5. Tracks P&L, inventory, drawdown

**Output**:
```
   as  baseline      net= 12.3456  maker_qty= 4.5678  maxDD=  2.1234  halted=False
```

### Read Results

```python
import pandas as pd

# Load fills
fills = pd.read_parquet("reports/backtests/as_baseline_fills.csv")
print(fills.head())

# Load equity curve
equity = pd.read_parquet("reports/backtests/as_baseline_equity.csv")
equity.plot(x="timestamp", y="equity")
```

---

## 🔍 EXAMPLE 8: Debug with Tests

### Run Specific Test

```powershell
cd backend/cpp/build
./lobx_tests.exe "[matching]"
```

**Runs only tests tagged with `[matching]`**.

### Add Your Own Test

Edit `backend/cpp/tests/test_orderbook.cpp`:

```cpp
TEST_CASE("My custom test", "[orderbook]") {
    OrderBook book;
    // Your test logic
    REQUIRE(book.empty());
}
```

Rebuild and run:
```powershell
cmake --build build
cd build
./lobx_tests.exe
```

---

## 🐍 EXAMPLE 9: Python Unit Test

Create `backend/python/tests/test_my_feature.py`:

```python
import lobx_cpp

def test_order_creation():
    order = lobx_cpp.Order(1, 10000, 100, is_buy=True)
    assert order.id == 1
    assert order.price == 10000
    assert order.qty == 100
    assert order.is_buy == True

def test_empty_book():
    book = lobx_cpp.OrderBook()
    assert book.best_bid() is None
    assert book.best_ask() is None
    assert book.empty() == True
```

Run:
```powershell
cd backend/python
pytest tests/test_my_feature.py -v
```

---

## 🚀 EXAMPLE 10: Modify and Extend

### A. Add a New Order Type (GTD - Good-Till-Date)

**1. Edit `enums.hpp`:**
```cpp
enum class TimeInForce : std::uint8_t {
    GTC = 0,
    IOC = 1,
    FOK = 2,
    GTD = 3,  // NEW
};
```

**2. Edit `order.hpp`:**
```cpp
struct Order {
    // ...
    std::chrono::system_clock::time_point expiry;  // NEW field
};
```

**3. Edit `order_book.cpp` matching logic:**
```cpp
// Check expiry before matching
if (order.tif == TimeInForce::GTD && order.expiry < now()) {
    return {};  // Expired, don't match
}
```

**4. Update pybind11:**
```cpp
py::enum_<TimeInForce>(m, "TimeInForce")
    .value("GTC", TimeInForce::GTC)
    .value("IOC", TimeInForce::IOC)
    .value("FOK", TimeInForce::FOK)
    .value("GTD", TimeInForce::GTD);  // NEW
```

**5. Rebuild:**
```powershell
cd backend/cpp
cmake --build build
```

**6. Test:**
```python
import lobx_cpp
order = lobx_cpp.Order(1, 10000, 100, True, lobx_cpp.TimeInForce.GTD)
print(order.tif)  # Should print 3
```

---

### B. Implement a New Strategy

Create `backend/python/lobx/strategy/my_strategy.py`:

```python
from lobx.strategy.base import Strategy, MarketState, QuotePair, Quote

class MyCustomStrategy(Strategy):
    def __init__(self, param1: float = 0.5):
        self.param1 = param1
    
    def on_tick(self, state: MarketState) -> QuotePair:
        # Your logic here
        bid_price = state.mid_price - self.param1
        ask_price = state.mid_price + self.param1
        
        return QuotePair(
            bid=Quote(price=bid_price, qty=0.001),
            ask=Quote(price=ask_price, qty=0.001)
        )
    
    def name(self) -> str:
        return "my-custom"
```

Use in backtest:
```python
from lobx.strategy.my_strategy import MyCustomStrategy

# In run_backtest.py, add to strategy factory
if name == "my-custom":
    return lambda: MyCustomStrategy(param1=args.param1)
```

---

## 📚 KEY TAKEAWAYS

1. **C++ engine is FAST**: Use for matching, Python for analytics
2. **pybind11 = zero overhead**: Call C++ from Python seamlessly
3. **Test-driven**: Write tests FIRST, then implement
4. **Backtest before live**: Validate on historical data
5. **Calibrate parameters**: Don't use defaults blindly
6. **Monitor everything**: Latency, P&L, inventory, drawdown

---

**Next: Pick ANY section from ULTRA_CONDENSED_COMPLETE_GUIDE.md and ask me to expand it further!**

**Or: Tell me what you want to BUILD next, and I'll guide you step-by-step.**
