# Model Calibration

Calibration in LOB-X focuses on fitting the Avellaneda-Stoikov (2008) inventory-based pricing model to empirical order book data.

## 1. Volatility ($\sigma$)
Volatility is computed using the standard deviation of log-returns, scaled to a per-second baseline.
**Time Units**: The live pipeline reports log-return volatility per $\sqrt{\text{second}}$, because the strategy pricing engine assumes $t$ is in seconds. It is dimensionless in log-price terms, not USD/second.
**Reference**: Campbell, Lo & MacKinlay (1997) *The Econometrics of Financial Markets*.

## 2. Fill Intensity Decay ($\kappa$)
In the AS model, the probability of an order fill decays exponentially as it is placed further from the mid-price: $\lambda(\delta) = A e^{-\kappa \delta}$.
**Calibration**: We estimate $\kappa$ by bucketing empirical fill rates against distance $\delta$ and performing an OLS regression in log-space.
**Quality Check**: We explicitly monitor the $R^2$ of this fit to ensure the assumption of exponential decay holds.

## 3. Risk Aversion ($\gamma$)
**Critical Distinction**: $\gamma$ is an *assumption*, not a calibrated parameter.
It represents the risk tolerance of the market maker and is derived here from the explicit maximum-inventory assumption and the real fitted kappa: $\gamma = \frac{1}{I_{max} \cdot \kappa}$. This is a project design heuristic, not a result from Avellaneda--Stoikov or a data fit.
**Warning**: Never treat $\gamma$ as a data-fit parameter. It is a strategic design choice.

## 4. Literature comparison

Avellaneda & Stoikov (2008), *High-frequency trading in a limit order book*, models order-arrival intensity as $A e^{-\kappa\delta}$. The paper does not supply a universal numerical kappa or gamma range: values depend on the asset, quote-distance unit, inventory boundary and horizon. LOB-X therefore compares the **functional form** only and reports kappa in ticks; it does not claim that a numerical value is literature-validated. DOI: [10.1080/14697680701381228](https://doi.org/10.1080/14697680701381228).

## 5. Comparison against Falces Marín et al. (2022)

Falces Marín et al. (2022) apply a genetic optimisation over AS parameters on real BTC-USD data and report:

- **Gamma (risk aversion):** optimised value ≈ **0.624**, explored range **[0.01, 0.9]**
- **Kappa (fill-intensity decay):** not reported as a single universal value; depends on the distance unit and session

**LOB-X calibrated values (as of 2026-08-06):**
- kappa = **NOT USABLE** — exponential fit R² < 0.50 floor on pooled data (14,290 trades); fit aborts rather than producing a nonsense value.
- gamma = **NOT DERIVED** — blocked upstream of a valid kappa. Previous un-gated value of 4.94 is confirmed unviable against the [0.01, 0.9] benchmark.

**Assessment:** Two fits were attempted on real overnight data (9,558,861 depth rows, 1,378,952 trades):

1. **Whole-tick resolution (0.01 USD buckets, 10 points):** R² = 0.355. Fills dominated by the best-bid/ask bucket (138,750 fills), ticks 2–10 approximately flat and non-monotone.
2. **Sub-tick resolution (0.001 USD buckets, 100 points):** R² = 0.119. The finer grid revealed that fills only land at 0.005 USD multiples (0.5, 1.5, 2.5 … half-ticks from mid), because BTCUSDT prices are constrained to 0.01 USD exchange ticks and mid sits at a half-tick. Sub-tick distances are mathematically impossible for this asset, so the finer grid introduces 9 empty bins between every real point, degrading the fit further.

**Citable conclusion:** The standard AS exponential fill-intensity model does not calibrate well for BTCUSDT's tight one-tick-spread regime at this data volume. The R² ≥ 0.50 gate correctly blocks both fits. No kappa or gamma value will be derived until a methodologically valid fit is produced (e.g. fitting only on the observed half-tick bins, or switching to a Hawkes-process fill model more suited to a 1-tick-spread asset).
