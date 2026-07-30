# Market Making — Avellaneda-Stoikov

## Model

$$r(s, q, t) = s - q \gamma \sigma^2 (T - t)$$

$$\delta^* = \gamma \sigma^2 (T - t) + \frac{2}{\gamma} \ln\!\left(1 + \frac{\gamma}{\kappa}\right)$$

Where:
- $s$ = mid price
- $q$ = signed inventory
- $\gamma$ = risk aversion
- $\sigma$ = per-tick volatility
- $T - t$ = remaining session time
- $\kappa$ = order flow intensity

## Implementation

`backend/python/lobx/models/avellaneda/`:
- `reservation_price.py` — computes $r$
- `optimal_spread.py` — computes $\delta^*$ and half-spread
- `market_maker.py` — `AvellanedaStoikovMM` trader that refreshes quotes each tick

## Parameters (defaults)

| Param | Default | Effect |
|-------|---------|--------|
| γ | 0.1 | Higher → tighter quotes when inventory builds |
| σ | 1.0 | Drives spread width |
| κ | 1.5 | Higher → tighter spread |
| qty | 5 | Quote size each side |

## Baseline Comparison

`NaiveMarketMaker` quotes a fixed spread regardless of inventory.
Run `scripts/run_simulation.py` with both enabled to compare P&L.
