# Research: can a calibrated market maker make money on BTCUSDT?

The original baseline study below covers calm and crash windows. The current
repository extends it to five real four-hour windows and nested chronological
selection. Full current tables and tuning grids are in
`results/research/SUMMARY.md`; raw numbers are in the adjacent JSON files.

## Current five-window extension

The adaptive research added live-volatility scaling, asymmetric Hawkes
widening, signed-volume toxicity, L1 queue pressure, a fee floor and an
adverse-side fair-value gate. Fair-value horizon and alpha-gate thresholds are
chosen on a later inner fold; final parameters are refit on the complete outer
training half before the unseen outer holdout.

At the market-maker rebate scenario, `AdaptiveGLFT alpha-gated` produced:

* calm +$0.026;
* crash +$0.135;
* volatile −$1.480;
* selloff +$0.437 (451 fills, 51.2% fee-adjusted 5 s fill-win rate,
  95% bootstrap PnL CI [+$0.054, +$1.067]).

These are mixed results, not evidence of a universal winner. The positive
results also depend on access to a −0.5 bp maker rebate. At ordinary VIP0,
active strategies remain negative except for a statistically unusable
selloff fee-aware result (+$0.344 on only five fills).

The latency-aware crash run selected the alpha threshold by maximizing the
worst inner-fold PnL across baseline and 150+150 ms execution. Its outer
holdout remained +$0.202 at 50+50 ms but fell to −$0.742 at 150+150 ms; no
tested threshold survived that larger delay.

A later robust-activation variant compares the selected threshold's normal
and 150+150 ms inner-fold PnL with zero. It abstains for the next holdout when
that worst-case score is non-positive. In two rolling selloff folds it skipped
the fold where the ordinary alpha-gated strategy lost −$0.054 and traded the
fold where it made +$0.064. Pooled robust-gated PnL was therefore +$0.064, but
on only 14 fills: encouraging risk selection, not sufficient promotion
evidence.

## Setup

* **Data.** Five Binance USD-M BTCUSDT futures windows with tick-by-tick L1
  and every trade (`data.md`): calm, crash, rally, volatile and selloff.
* **Protocol.**
  1. Split each window in time: first 2 h in-sample, last 2 h out-of-sample.
  2. Under `--nested`, split the training half again: calibrate σ, A, k,
     Hawkes and fair-value candidates on the first inner fold.
  3. Choose the fair-value horizon, γ and alpha gates on the later inner fold:
     γ is the best net PnL among
     settings with at least 100 fills/h, chosen separately per fee tier.
  4. Refit the selected model form on the full training half, then run every
     strategy once out-of-sample with frozen parameters.
* **Cross-regime test.** Calibrate and tune on the whole calm window, then
  trade the whole crash window unchanged.
* **Execution.** 0.001 BTC per quote, position limit 0.01 BTC.
  * Latency: 5 ms feed + 10 ms order.
  * Queue: probabilistic.
  * Fills: post-only, flattened with a taker order at the end.
  * Details in `execution_model.md`.
* **Fee tiers.** VIP0 (maker 2.0 bp, taker 5.0 bp) and the market-maker
  programme (maker −0.5 bp rebate, taker 1.7 bp).
* **Uncertainty.** A paired moving-block bootstrap of per-minute PnL gives 95%
  intervals and P(beats FixedSpread).

Strategies:

| Name | Quotes |
|---|---|
| NoTrade | nothing (control) |
| FixedSpread | joins the best bid and ask |
| InventorySkew | joins the touch and shifts half a tick per lot of inventory |
| AS | Avellaneda–Stoikov, calibrated σ and k, τ = 60 s |
| GLFT | Guéant–Lehalle–Fernandez-Tapia, calibrated σ, A, k |
| GLFT+Hawkes+FV | GLFT around a fitted fair value, widened by Hawkes intensity, one-sided in bursts |
| GLFT+HFV fee-aware | the above, but drops a side whose edge cannot cover the maker fee |
| AdaptiveGLFT | GLFT with live-vol scaling, asymmetric Hawkes, toxicity and queue-pressure widening |
| AdaptiveGLFT fee-floor | moves quotes beyond fee plus margin instead of simply stopping |
| AdaptiveGLFT alpha-gated | pauses the side exposed to a validated short-horizon prediction |
| AdaptiveGLFT alpha-gated fee-floor | combines adverse-side pause and fee floor |
| AdaptiveGLFT robust-gated | the alpha-gated strategy, active only when normal and latency-stressed inner PnL both beat zero |
| AdaptiveGLFT robust-gated fee-floor | robust activation plus the fee floor |

## Calibration

| | calm | crash |
|---|---|---|
| σ in-sample / out-of-sample (USD/√s) | 2.22 / 1.87 | 10.9 / 28.0 |
| A (1/s), k (1/USD), fit R² | 0.30, 0.85, 0.79 | 3.36, 0.57, 0.81 |
| Hawkes branching ratio buy / sell | 0.68 / 0.64 | 0.18 / 0.09 |
| Fair-value R² in-sample / out-of-sample | 0.10 / 0.13 | 0.02 / −0.01 |
| Tuned γ, AS / GLFT (1/USD) | 0.03 / 1 | 0.003 / 3 |

Out-of-sample volatility in the crash is 2.6 times in-sample. That is the hard
case for a model calibrated on the past, and it is deliberate.

## Results

Net PnL in USD over 2 h out-of-sample (4 h for the cross-regime test), with
the 95% bootstrap interval. Break-even is the maker fee in bp at which the
strategy nets zero; negative means it needs a rebate that large. Edge per fill
is shown per BTC.

### Calm

| Strategy | VIP0 | MM rebate | Fills/h | Edge/BTC | Adverse 5 s | Break-even |
|---|---|---|---|---|---|---|
| FixedSpread | −34.2 [−39.6, −30.1] | −2.89 [−5.41, −1.02] | 914 | $0.02 | 0.52 bp | −0.73 bp |
| InventorySkew | −30.9 [−36.0, −26.9] | −3.51 [−5.74, −1.80] | 788 | $0.04 | 0.61 bp | −0.83 bp |
| AS | −8.16 [−9.40, −7.33] | −0.69 [−1.25, −0.27] | 214 | $0.75 | 0.69 bp | −0.74 bp |
| GLFT | −9.16 [−10.4, −8.03] | −0.68 [−1.34, −0.28] | 243 | $0.73 | 0.65 bp | −0.70 bp |
| GLFT+Hawkes+FV | **−7.25** [−8.46, −6.38] | **−0.47** [−0.90, −0.18] | 194 | $0.98 | 0.69 bp | −0.67 bp |
| GLFT+HFV fee-aware | 0 (never quotes) | −0.47 [−0.90, −0.18] | 0 / 194 | | | |

### Crash

| Strategy | VIP0 | MM rebate | Fills/h | Edge/BTC | Adverse 5 s | Break-even |
|---|---|---|---|---|---|---|
| FixedSpread | −709 [−832, −615] | −79.7 [−101, −66.5] | 19,200 | $0.31 | 0.80 bp | −0.82 bp |
| InventorySkew | −693 [−821, −598] | −86.1 [−110, −74.0] | 18,393 | $0.32 | 0.87 bp | −0.85 bp |
| AS | −9.39 [−12.1, −3.24] | **+1.19** [+0.04, +2.33] | 315 | $13.4 | 2.44 bp | −0.22 bp |
| GLFT | −18.3 [−22.5, −12.5] | **+4.64** [−0.06, +8.62] | 689 | $13.7 | 2.13 bp | +0.01 bp |
| GLFT+Hawkes+FV | −13.5 [−16.3, −9.62] | +1.36 [−0.39, +2.88] | 446 | $12.9 | 2.28 bp | −0.27 bp |
| GLFT+HFV fee-aware | −10.6 [−19.2, −0.74] | +0.98 [−0.44, +2.71] | 31 / 436 | $33.7 | 9.02 bp | |

### Cross-regime: calibrated on calm, traded through the crash

| Strategy | VIP0 | MM rebate | Fills/h |
|---|---|---|---|
| FixedSpread | −1,027 [−1,278, −843] | −108 [−143, −81.8] | 13,991 |
| AS | −0.31 [−0.34, 0.00] | +0.03 [0.00, +0.09] | 5 |
| GLFT | −3.53 [−4.81, −2.15] | **+0.69** [+0.09, +1.38] | 63 |
| GLFT+Hawkes+FV | −0.11 [−0.26, 0.00] | −0.01 [−0.02, +0.01] | 2 |

Fair-value R² falls from +0.12 to −0.08 across the regime change, so the signal
is worse than predicting no change.

### Stress tests

These run in the same pass on the same out-of-sample data. They vary the full
model (GLFT+Hawkes+FV) except in the last row.

| Stress (MM rebate) | calm | crash |
|---|---|---|
| baseline (15 ms round trip) | −0.47 | +1.36 |
| latency 50 + 50 ms | −0.49 | −0.32 |
| latency 150 + 150 ms | −0.56 | −3.73 |
| requote limit 500 ms | −0.50 | −2.24 |
| optimistic queue | +1.00 | +2.48 |
| FixedSpread with optimistic queue | **+54.2** (vs −2.89) | **+106** (vs −79.7) |

## Conclusions

1. **Touch-quoting BTCUSDT loses at every fee tier.**
   * Even with the 0.5 bp market-maker rebate, FixedSpread loses in both
     regimes, with intervals entirely below zero.
   * It needs a 0.73–0.82 bp rebate to break even. Its 5 s adverse selection
     (0.5–0.8 bp) is much larger than the spread it captures: a one-tick
     spread at $70k is 0.014 bp.
   * This is the economics of the most competitive futures book in crypto,
     not a bug.
2. **Calibrated models beat touch-quoting decisively.** At VIP0, AS, GLFT and
   GLFT+Hawkes+FV lose 3.7–4.7 times less than FixedSpread in the calm window
   and 39–75 times less in the crash. The paired intervals on the difference
   exclude zero in every case. The gain comes from quoting deeper (about $4
   from mid in calm) and capturing tens of times more edge per fill
   ($0.73–0.98 per BTC against about $0.02 in calm).
3. **At the market-maker tier, deep quoting in volatile markets is positive
   but fragile.**
   * Crash, out-of-sample:
     * AS: +$1.19, P(>0) = 0.98, interval just above zero.
     * GLFT: +$4.64, P(>0) = 0.97.
     * GLFT's break-even maker fee is ≈ 0 bp. It roughly breaks even at
       VIP9's 0 bp maker fee and profits only with a rebate.
   * Calm-calibrated GLFT in the crash: +$0.69, interval [+0.09, +1.38].
   * The original enhanced model lost at 100 ms round trip. The later
     alpha-gated candidate remained positive at 50+50 ms in the crash but
     lost at 150+150 ms. This remains an edge for fast participants only.
4. **Hawkes and fair value help in calm markets, and in a regime break they
   act as a brake.**
   * In calm data they cut the loss by about a fifth against plain GLFT
     (−7.25 vs −9.16 at VIP0) and raise edge per fill by 34%. This
     comparison is not bootstrapped against GLFT directly.
   * In the cross-regime test the crash keeps the intensity ratio far above
     the calm stationary level. The Hawkes layer widens and pauses quoting,
     and the strategy nearly stops trading.
   * At VIP0 that saves $3.4 compared with plain GLFT. At the rebate it
     forgoes plain GLFT's $0.69.
   * The fair-value signal (R² 0.13 out-of-sample in calm) does not transfer
     to the crash.
5. **The fill model decides the answer.** The optimistic queue turns
   FixedSpread's calm loss of −$2.89 into +$54, and its crash loss of −$80
   into +$106, with 10 times the fills and almost no adverse selection.
   Earlier versions of this project used that fill model.
6. **The fee-aware filter does what it says.** At VIP0 in calm data it never
   quotes, because no quote can cover a 2 bp fee. That is the correct answer
   and equals NoTrade.

### Limits of this evidence

* Five 4-hour windows from one venue, one symbol and one historical month.
* About 120 one-minute observations per out-of-sample run. The bootstrap
  intervals are honest about that, and several positive results sit within
  noise of zero.
* L1 history makes the queue estimate for quotes behind the touch
  pessimistic (`execution_model.md`).
* There is no market impact from our own orders, and latency is a constant.
* Sharpe is annualised from a two-hour run and is useful only as a ranking.

### What would change the answer

* Lower latency, measured co-located. The stress rows show this matters more
  than any model choice.
* A real alpha signal on a horizon longer than the queue wait. The 2 s
  imbalance signal is too weak.
* Smaller-tick or wider-spread instruments, where the spread pays for
  adverse selection. `lobx_cli scan` ranks Binance futures by spread vs fee.
* L2 history (collector captures), to remove the pessimism for deep quotes.
