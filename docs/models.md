# Models and calibration

Every quoting model in LOB-X uses one set of units. Most of the bugs this
project started with were unit bugs, so the units come first.

| Symbol | Meaning | Unit | Source |
|---|---|---|---|
| S | mid price | USD | data |
| σ | mid-price diffusion, dS = σ dW | USD/√s | `analytics.volatility` |
| q | inventory | lots (position / order size) | account |
| γ | risk aversion | 1/USD per lot | hyperparameter, tuned in-sample |
| A | rate of market orders reaching the mid, per side | 1/s | `fit_arrival` |
| k, κ | decay of fill intensity with depth, λ(δ) = A·e^(−kδ) | 1/USD | `fit_arrival` |
| τ | risk horizon (AS) | s | 60 s, fixed |
| μ, α, β | Hawkes baseline, jump, decay | 1/s | `fit_hawkes` (MLE) |

## Volatility (`analytics/volatility.py`)

σ is the standard deviation of *price* changes per √second, not a log return.
The mid is sampled on a 1 s grid using the last value before each grid point,
so the estimate is not inflated by bid-ask bounce. Then:

* backtests use realized σ² = mean(ΔS²) / Δt over the in-sample window;
* live trading uses an EWMA of ΔS² with a 120 s half-life, seeded from the
  calibration.

Calm BTCUSDT (30 Mar 2024) has σ ≈ 2.2 USD/√s (0.3 bp/√s). The 5 Mar 2024
crash has σ ≈ 11 USD/√s in-sample and 28 USD/√s out-of-sample.

**The original bug.** The old estimator returned a per-update log return of
about 3·10⁻⁶. With that σ, γσ²τ ≈ 0 and the AS spread collapsed to the pure
liquidity term (2/γ)·ln(1+γ/κ). With the defaults γ = 0.1 and κ = 1.5
(the numbers from the AS paper's stock example) that is $1.29. It was
the same in every market regime and had nothing to do with BTC.

## Fill intensity: A and k (`analytics/calibration.fit_arrival`)

Every market order is reduced to its worst execution price. Its depth is
measured against the mid that was visible *before* it arrived:
δ = price − mid for a buy and mid − price for a sell. For a grid of depths
(half a tick, then one-tick steps):

```
λ(δ) = #{market orders reaching depth ≥ δ} / (2 · T)     (per side, per second)
ln λ(δ) = ln A − k·δ                                      (least squares)
```

On calm BTCUSDT this gives A = 0.30/s, k = 0.85/USD with R² = 0.79.
A quote $1 from the mid is reached by about 0.13 market orders per second per
side. This is an upper bound on our fill rate: queue position decides whether
those orders reach *our* order, and the simulator models that separately.

## Avellaneda–Stoikov (`models/avellaneda`, `strategy/avellaneda.py`)

```
reservation r   = S − q·γ·σ²·τ
full spread  δ* = γ·σ²·τ + (2/γ)·ln(1 + γ/κ)
bid, ask        = r ∓ δ*/2
```

A 24/7 market has no terminal time, so τ is held at 60 s (a rolling horizon)
rather than decaying to zero. κ is the calibrated k in 1/USD, and q is in
lots.

**Inventory sign check.** With q = +3 lots, γ = 0.03, σ = 2.2 and τ = 60, the
reservation price is $26 below mid. When long, the ask moves towards (or
onto) the touch and the bid moves away. The model therefore quotes more
aggressively on the side that *reduces* inventory. That is the intended
behaviour, and `tests/test_models.py` asserts the sign.

## GLFT (`models/glft.py`, `strategy/glft.py`)

The infinite-horizon approximation of Guéant, Lehalle and Fernandez-Tapia
(2013). Unlike AS, its skew does not depend on time remaining:

```
c1 = ln(1 + γ/k) / γ
c2 = sqrt( γ / (2·A·k) · (1 + γ/k)^(k/γ + 1) )
half-spread = c1 + σ·c2/2
skew        = σ·c2           (USD per lot of inventory)
bid depth   = half-spread + skew·q
ask depth   = half-spread − skew·q
```

`GLFTStrategy` adds four optional layers, each switched on in its own
strategy variant so its contribution can be measured:

1. **Fair value.** It quotes around S + α instead of S, with
   α = b_flow·flow·σ + b_obi·obi·σ. Here obi is L1 queue imbalance and flow
   is the Hawkes buy−sell intensity imbalance (below).
2. **Hawkes widening.** Both depths are multiplied by
   1 + 0.5·max(0, ratio − 1).
3. **Hawkes pause.** In a burst (ratio ≥ 3) it quotes only the side that
   reduces inventory.
4. **Fee-aware filter.** It drops a side whose distance from fair value does
   not cover the maker fee.

## Hawkes order flow (`models/hawkes`, `strategy/features.py`)

Buy and sell market orders are each fitted as an exponential Hawkes process:

```
λ(t) = μ + Σ_{tᵢ<t} α·e^(−β(t − tᵢ))       branching ratio n = α/β < 1
stationary mean = μ / (1 − n)
```

* **Fit.** Exact log-likelihood with the O(N) recursion. L-BFGS-B works on
  (log μ, logit n, log β), so every trial point is stationary. Several random
  starts are used.
* **What counts as an event.** Trades that share a millisecond and an
  aggressor side are counted as one market order, so a single sweep across
  five levels is one event, not five.
* **The gauge.** It reports ratio = (λ_buy + λ_sell) / (stationary_buy +
  stationary_sell) and flow = (λ_buy − λ_sell)/(λ_buy + λ_sell). The regime
  is QUIET below 0.7, NORMAL below 1.5, ELEVATED below 3 and BURST at 3 or
  above. All four are computed from the fitted parameters.
* **Live.** The terminal and the paper-trading loop start from default
  parameters and are labelled "default params, fitting…". They refit by MLE
  in a worker thread every 60 s from the last 20,000 market orders per side.
  After a refit, the recent events are replayed so the intensity does not
  jump back to its baseline.

Calibrated values:
* Calm window: n ≈ 0.68 (buy) and 0.64 (sell). About two thirds of market
  orders are triggered by earlier ones, the usual finding for liquid futures.
* Crash window: n ≈ 0.18 and 0.09.

The fit uses only the first 20,000 market orders per side of the in-sample
window, which keeps MLE time to seconds. In the crash window that covers
roughly the first half-hour, so its Hawkes parameters are the least reliable
of the calibration.

## Fair value (`analytics/calibration.fit_fair_value`)

A ridge regression (λ = 10⁻⁶) of the mid change 2 s ahead on (flow·σ, obi·σ),
sampled every 100 ms in-sample. Scaling by σ keeps the coefficients
dimensionless across regimes.

The microprice deviation was dropped. With a one-tick spread it equals
obi·tick/2, which is collinear with obi. Its fitted coefficient came out
negative, a sign of collinearity rather than signal.

| Window | R² in-sample | R² out-of-sample |
|---|---|---|
| calm | 0.10 | 0.13 |
| crash | 0.02 | −0.01 |
| calm → crash | 0.12 | −0.08 |

Short-horizon order-book signal is real in calm markets and does not survive
a regime change. This is one of the main findings in `research.md`.

## γ is a hyperparameter

γ is a risk preference, not a market property, so it is not "calibrated".
`backtest/research.tune_gamma` runs AS and GLFT over
γ ∈ {10⁻⁴ … 10} on the in-sample window, all in one pass. Among settings that
fill at least 100 times per hour, it picks the one with the best in-sample
net PnL. Without that floor, when every setting loses, the "best" γ would be
the one that quotes so wide it never trades.

## References

* M. Avellaneda, S. Stoikov, *High-frequency trading in a limit order book*,
  Quantitative Finance 8 (2008).
* O. Guéant, C.-A. Lehalle, J. Fernandez-Tapia, *Dealing with the inventory
  risk*, Mathematics and Financial Economics 7 (2013).
* A. G. Hawkes, *Spectra of some self-exciting and mutually exciting point
  processes*, Biometrika 58 (1971).
* E. Bacry, I. Mastromatteo, J.-F. Muzy, *Hawkes processes in finance*,
  Market Microstructure and Liquidity (2015).
* E. Rigtorp, *Estimating order queue position* (2013); N. Kim, hftbacktest
  queue models.
* Á. Cartea, S. Jaimungal, J. Penalva, *Algorithmic and High-Frequency
  Trading*, CUP (2015).
