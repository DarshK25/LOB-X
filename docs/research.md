# Research: can a calibrated market maker make money on BTCUSDT?

All numbers are out-of-sample, from `python scripts/run_research.py --cross calm crash`.
Full tables, including the tuning grids, are in
`results/research/SUMMARY.md`, and raw numbers in the JSON files next to it.

## Setup

* **Data.** Binance USD-M BTCUSDT futures with tick-by-tick L1 and every
  trade (`data.md`). The calm window is 30 Mar 2024, 08–12 UTC. The crash
  window is 5 Mar 2024, 13–17 UTC.
* **Protocol.**
  1. Split each window in time: first 2 h in-sample, last 2 h out-of-sample.
  2. Calibrate σ, A, k, Hawkes and the fair-value model on in-sample data
     only.
  3. Choose γ for AS and GLFT on in-sample data only: best net PnL among
     settings with at least 100 fills/h, chosen separately per fee tier.
  4. Run every strategy once out-of-sample, with frozen parameters.
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
   * The edge is gone at a 100 ms round trip and at a 500 ms requote limit.
     That is the signature of an edge that belongs to fast participants.
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

* Two 4-hour windows from one venue and one symbol.
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
