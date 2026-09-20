# HFTENGINE Queue Model Reference

**Source:** `_reference/hftengine/hftbacktest/hftbacktest/src/backtest/models/queue.rs`

## Verified Implementation

**PowerProbQueueFunc3** (default, n=3):

```rust
fn prob(&self, front: f64, back: f64) -> f64 {
    1.0 - self.f(front / (front + back))
}

fn f(&self, x: f64) -> f64 {
    x.powf(self.n)
}
```

**Formula:**
```
prob = 1 - (front_qty / (front_qty + back_qty))^n
```

## Semantics

`prob` = Probability that order advances when depth **decreases** at level

NOT direct fill probability. Fill occurs when `front_qty <= 0`.

## Queue Position Lifecycle

### 1. Order Submission
```rust
fn new_order(&self, order: &mut Order, depth: &MD) {
    q.front_q_qty = depth.bid_qty_at_tick(order.price_tick);  // or ask
}
```
Conservative: Order joins **back** of queue. `front_qty = total_level_qty`.

### 2. Trade at Level
```rust
fn trade(&self, order: &mut Order, qty: f64, _depth: &MD) {
    q.front_q_qty -= qty;
    q.cum_trade_qty += qty;
}
```
Unconditional queue consumption. Track cumulative to avoid double-counting.

### 3. Depth Change at Level
```rust
fn depth(&self, order: &mut Order, prev_qty: f64, new_qty: f64, _depth: &MD) {
    let mut chg = prev_qty - new_qty;
    chg -= q.cum_trade_qty;  // Subtract already-counted trades
    q.cum_trade_qty = 0.0;
    
    if chg < 0.0 {  // Depth increase
        q.front_q_qty = q.front_q_qty.min(new_qty);
        return;
    }
    
    // Depth decrease: probabilistic advancement
    let front = q.front_q_qty;
    let back = prev_qty - front;
    let prob = self.prob.prob(front, back);
    
    let est_front = front - (1.0 - prob) * chg + (back - prob * chg).min(0.0);
    q.front_q_qty = est_front.min(new_qty);
}
```

### 4. Fill Decision
```rust
fn is_filled(&self, order: &mut Order, depth: &MD) -> f64 {
    let exec = (-q.front_q_qty / depth.lot_size()).round() as i64;
    if exec > 0 {
        q.front_q_qty = 0.0;
        (exec as f64) * depth.lot_size()
    } else {
        0.0
    }
}
```

Fill when `front_qty <= 0`.

## Exponent Behavior

- **n = 1**: Linear. `prob = back/(front+back)`. Proportional position.
- **n = 3**: HFTENGINE default. More conservative advancement.
- **n → ∞**: Very conservative. Order almost never advances except via trades.

## Key Differences from Documentation

1. **Not fill probability**: `prob` is queue advancement probability on depth decrease
2. **Depth vs trades**: Separate handling. Trades reduce queue unconditionally.
3. **Cumulative tracking**: `cum_trade_qty` prevents double-counting
4. **Conservative initialization**: Order always joins back of queue

## LOB-X Implementation

Implemented in `backend/python/lobx/execution/queue_model.py`:
- `PowerProbFunc3(n=3.0)` - probability model
- `QueuePosition` - state tracking
- `ProbabilisticQueueModel` - lifecycle management
- Deterministic RNG for reproducibility
