# CHAPTER 1: PROBABILITY FOUNDATIONS FOR QUANT FINANCE

**Goal**: Build intuition for randomness, distributions, and expectation—the foundation of ALL quantitative models.

---

## 1.1 PROBABILITY BASICS

### Sample Space and Events

**Sample Space (Ω)**: Set of all possible outcomes
**Event (A)**: Subset of Ω

**Example - Coin Flip**:
```
Ω = {H, T}
Event A = {H} (getting heads)
P(A) = 0.5
```

**Example - Stock Price Tomorrow**:
```
Ω = [0, ∞) (all possible prices)
Event A = {price > $100}
P(A) = ?  (This is what we model!)
```

### Axioms of Probability (Kolmogorov)

1. **Non-negativity**: P(A) ≥ 0 for all events A
2. **Normalization**: P(Ω) = 1 (something must happen)
3. **Additivity**: If A and B are disjoint, P(A ∪ B) = P(A) + P(B)

### Conditional Probability

**Formula**:
```
P(A|B) = P(A ∩ B) / P(B)
```

**Read as**: "Probability of A given B has occurred"

**Example - Trading**:
```
A = "stock goes up tomorrow"
B = "stock went up today"

P(A|B) might be ≠ P(A) if there's momentum!
```

### Independence

**Definition**: A and B are independent if:
```
P(A ∩ B) = P(A) · P(B)
or equivalently: P(A|B) = P(A)
```

**Intuition**: Knowing B tells you NOTHING about A

**Example**:
- Coin flips: Independent
- Stock returns on consecutive days: NOT independent (autocorrelation exists)

---

## 1.2 RANDOM VARIABLES

### Definition

A **random variable** X is a function X: Ω → ℝ that assigns a number to each outcome.

**Example - Dice Roll**:
```
Outcome: ⚀ ⚁ ⚂ ⚃ ⚄ ⚅
X:       1  2  3  4  5  6
```

### Discrete vs Continuous

**Discrete**: Countable values (dice, coin flips)
- Described by **Probability Mass Function (PMF)**: p(x) = P(X = x)

**Continuous**: Uncountable values (stock prices, returns)
- Described by **Probability Density Function (PDF)**: f(x)
- P(a < X < b) = ∫[a,b] f(x) dx

**KEY INSIGHT**: For continuous X, P(X = exact value) = 0!
(Probability of EXACTLY $100.00000... is zero; we talk about ranges)

---

## 1.3 EXPECTATION (Mean)

### Definition

**Discrete**:
```
E[X] = Σ x · P(X = x)
```

**Continuous**:
```
E[X] = ∫ x · f(x) dx
```

**Interpretation**: "Center of mass" of the distribution

### Properties (CRITICAL for finance!)

1. **Linearity**: E[aX + b] = a·E[X] + b
2. **Additivity**: E[X + Y] = E[X] + E[Y] (ALWAYS true, even if not independent!)
3. **Multiplicativity** (IF independent): E[XY] = E[X]·E[Y]

**Trading Example**:
```python
# Expected return of a portfolio
# weights = [0.6, 0.4]
# returns = [0.05, 0.08]

E[portfolio] = 0.6 * 0.05 + 0.4 * 0.08 = 0.062 = 6.2%

# Linearity: E[w1·R1 + w2·R2] = w1·E[R1] + w2·E[R2]
```

---

## 1.4 VARIANCE AND STANDARD DEVIATION

### Variance

**Definition**:
```
Var(X) = E[(X - E[X])²] = E[X²] - (E[X])²
```

**Interpretation**: "Spread" around the mean

**Alternative form** (easier to compute):
```
Var(X) = E[X²] - μ²   where μ = E[X]
```

### Standard Deviation

```
σ = √Var(X)
```

**Why σ instead of Var?**: Same units as X (if X is in dollars, σ is in dollars, Var is in dollars²)

### Properties

1. Var(aX + b) = a²·Var(X)  (constant shifts don't change variance!)
2. Var(X + Y) = Var(X) + Var(Y) + 2·Cov(X,Y)

**Example**:
```python
import numpy as np

returns = [0.05, -0.02, 0.08, -0.01, 0.03]  # Daily returns

mean_return = np.mean(returns)  # 0.026
variance = np.var(returns)      # 0.00184
std_dev = np.std(returns)       # 0.0429 = 4.29%
```

---

## 1.5 COVARIANCE AND CORRELATION

### Covariance

**Definition**:
```
Cov(X, Y) = E[(X - E[X])(Y - E[Y])] = E[XY] - E[X]·E[Y]
```

**Interpretation**:
- Cov > 0: X and Y tend to move together
- Cov < 0: X and Y tend to move opposite
- Cov = 0: Uncorrelated (but NOT necessarily independent!)

**Problem with Cov**: Units are (unit of X) × (unit of Y), hard to interpret

### Correlation

**Definition**:
```
ρ(X, Y) = Cov(X, Y) / (σ_X · σ_Y)
```

**Range**: ρ ∈ [-1, 1]
- ρ = 1: Perfect positive linear relationship
- ρ = -1: Perfect negative linear relationship
- ρ = 0: Uncorrelated

**Key Property**: Correlation measures LINEAR relationships only!

**Example**:
```python
import numpy as np

# BTC and ETH returns
btc_returns = [0.05, -0.02, 0.08, -0.01, 0.03]
eth_returns = [0.04, -0.01, 0.06,  0.00, 0.02]

cov_matrix = np.cov(btc_returns, eth_returns)
print(cov_matrix)
# [[0.00184, 0.00137],
#  [0.00137, 0.00104]]

corr_matrix = np.corrcoef(btc_returns, eth_returns)
print(corr_matrix)
# [[1.000, 0.992],
#  [0.992, 1.000]]
# High correlation = 0.992 (move together)
```

---

## 1.6 IMPORTANT DISTRIBUTIONS

### 1.6.1 Normal (Gaussian) Distribution

**PDF**:
```
f(x) = (1 / √(2πσ²)) · exp(-(x-μ)² / (2σ²))
```

**Notation**: X ~ N(μ, σ²)

**Properties**:
- Symmetric around μ
- 68% within μ ± σ
- 95% within μ ± 2σ
- 99.7% within μ ± 3σ

**Central Limit Theorem (CLT)**:
```
Sum of many independent random variables → Normal distribution
```

**Why critical in finance?**: 
- Returns are often modeled as Normal (though this is an APPROXIMATION—real returns have fat tails)
- Brownian motion = continuous-time limit of Normal random walk

**Visualization**:
```
      ╱╲
     ╱  ╲         68% within 1σ
    ╱    ╲        95% within 2σ
   ╱      ╲       99.7% within 3σ
──╱────────╲──────────────
  μ-2σ  μ  μ+2σ
```

### Log-Normal Distribution

**If X ~ N(μ, σ²), then Y = e^X ~ LogNormal(μ, σ²)**

**Why critical?**: Stock prices are log-normal (can't be negative, right-skewed)

**Real-world reasoning**:
1. Stock prices can't go below 0 (limited liability)
2. Returns are (approximately) Normal
3. If returns are Normal, price = exp(sum of returns) is Log-Normal

**Visual difference**:
```
Normal:        Log-Normal:
   ╱╲             ╱╲_____
  ╱  ╲           ╱  ╲     ╲___
 ╱    ╲         ╱    ╲        ╲___
╱      ╲       ╱      ╲___________╲___
        (symmetric)    (right-skewed)
```

**Key Property**:
```
If S_t = S_0 · e^X where X ~ N(μt, σ²t)
then S_t ~ LogNormal
```

**Mean and Variance**:
```
E[Y] = exp(μ + σ²/2)
Var(Y) = exp(2μ + σ²) · (exp(σ²) - 1)
```

**Why the σ²/2 term?** This is the SAME Itô correction from GBM!

**Example**:
```python
import numpy as np
import matplotlib.pyplot as plt

# Generate log-normal stock prices
S0 = 100
mu = 0.10  # 10% annual return
sigma = 0.20  # 20% annual vol
T = 1.0
n_paths = 10000

# Method 1: Via normal returns
log_returns = np.random.normal((mu - 0.5*sigma**2)*T, sigma*np.sqrt(T), n_paths)
ST_method1 = S0 * np.exp(log_returns)

# Method 2: Direct log-normal
ST_method2 = np.random.lognormal(np.log(S0) + (mu - 0.5*sigma**2)*T, 
                                  sigma*np.sqrt(T), n_paths)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

ax1.hist(ST_method1, bins=50, alpha=0.7, density=True)
ax1.axvline(np.mean(ST_method1), color='r', linestyle='--', 
            label=f'Mean={np.mean(ST_method1):.2f}')
ax1.axvline(S0*np.exp(mu*T), color='g', linestyle='--', 
            label=f'Theoretical={S0*np.exp(mu*T):.2f}')
ax1.set_title("Terminal Stock Price Distribution")
ax1.legend()

ax2.hist(np.log(ST_method1/S0), bins=50, alpha=0.7, density=True)
ax2.set_title("Log-Returns Distribution (Should be Normal)")
plt.show()
```

**Financial implication**:
- Stock can go up by 100%, 200%, 1000% (unbounded)
- Stock can only go down by 100% (lose everything)
- This asymmetry is captured by log-normal, NOT normal

---

### 1.6.3 Exponential Distribution

**PDF**:
```
f(t) = λ · exp(-λt)  for t ≥ 0
```

**Interpretation**: Time until next event (e.g., time between trades)

**Property**: Memoryless!
```
P(T > s+t | T > s) = P(T > t)
```
("Given I've waited s seconds, probability of waiting t more is same as at start")

**Example**:
```python
import numpy as np
import matplotlib.pyplot as plt

# Time between trades (average 1 trade per 10 seconds)
lambda_rate = 0.1
samples = np.random.exponential(1/lambda_rate, 1000)

plt.hist(samples, bins=50, density=True)
plt.xlabel("Time (seconds)")
plt.ylabel("Density")
plt.title("Exponential: Time Between Trades")
plt.show()
```

---

## 1.7 LAW OF LARGE NUMBERS (LLN)

### Weak Law

**Statement**:
```
As n → ∞, sample mean converges to population mean in probability
X̄_n → E[X]
```

**Intuition**: Averaging many samples gives you the true mean

**Trading Application**: Backtesting over many scenarios gives expected P&L

### Strong Law

**Statement**:
```
Sample mean converges almost surely to population mean
P(lim X̄_n = E[X]) = 1
```

**Difference**: Strong law is about convergence of paths, weak law is about distributions

---

## 1.8 CENTRAL LIMIT THEOREM (CLT)

### Statement

**Given**: X₁, X₂, ..., Xₙ i.i.d. with mean μ and variance σ²

**Then**:
```
√n · (X̄ₙ - μ) / σ  →  N(0, 1)  as n → ∞
```

**Or equivalently**:
```
X̄ₙ ~ N(μ, σ²/n)  approximately for large n
```

**Intuition**: Sum of many random variables → Normal, regardless of original distribution!

**Visualization**:
```python
import numpy as np
import matplotlib.pyplot as plt

# Roll a die 10,000 times, take average of every 30 rolls
n = 30
num_samples = 10000

means = []
for _ in range(num_samples):
    rolls = np.random.randint(1, 7, size=n)
    means.append(np.mean(rolls))

# Plot histogram
plt.hist(means, bins=50, density=True, alpha=0.7)

# Overlay theoretical Normal
mu = 3.5  # E[die roll] = (1+2+3+4+5+6)/6
sigma = np.sqrt(35/12)  # Var(die) = 35/12
x = np.linspace(2, 5, 100)
plt.plot(x, (1/np.sqrt(2*np.pi*sigma**2/n)) * np.exp(-(x-mu)**2 / (2*sigma**2/n)))

plt.xlabel("Sample Mean")
plt.ylabel("Density")
plt.title("CLT: Means of 30 Die Rolls")
plt.show()
```

**Output**: Bell curve! Even though individual die rolls are uniform.

---

## 1.9 MOMENT GENERATING FUNCTIONS (MGF)

### Definition

**MGF of X**:
```
M_X(t) = E[e^(tX)] = ∫ e^(tx) f(x) dx
```

**Why useful?**:
1. Uniquely determines distribution
2. Easy to compute moments: E[X^n] = M^(n)(0)
3. Sum of independent r.v.'s: M_{X+Y}(t) = M_X(t) · M_Y(t)

**Example - Normal Distribution**:
```
If X ~ N(μ, σ²), then
M_X(t) = exp(μt + σ²t²/2)
```

**Application in Finance**: Pricing options (Laplace transforms)

---

## 1.10 CONDITIONAL EXPECTATION

### Definition

**Given Y, the conditional expectation E[X|Y] is a RANDOM VARIABLE** (function of Y)

**Formula (discrete)**:
```
E[X|Y=y] = Σ x · P(X=x | Y=y)
```

**Law of Total Expectation (Tower Property)**:
```
E[X] = E[E[X|Y]]
```

**Intuition**: "Expected value = weighted average of conditional expectations"

**Example - Market Making**:
```python
# X = profit from next trade
# Y = order book imbalance

# If imbalance > 0 (buy pressure):
E[X | Y > 0] = 0.5  # Expect positive fill on ask side

# If imbalance < 0 (sell pressure):
E[X | Y < 0] = -0.3  # Expect adverse selection

# Overall:
E[X] = P(Y > 0) · E[X|Y>0] + P(Y < 0) · E[X|Y<0]
     = 0.5 · 0.5 + 0.5 · (-0.3) = 0.1
```

---

## 1.11 JENSEN'S INEQUALITY

### Statement

**If φ is convex and X is a random variable**:
```
φ(E[X]) ≤ E[φ(X)]
```

**If φ is concave**:
```
φ(E[X]) ≥ E[φ(X)]
```

**Intuition**: "Expected value of a function ≠ function of expected value" (unless linear)

**Critical Example - Utility**:
```
U(x) = -e^(-γx)  (exponential utility, CONCAVE)

E[U(W)] ≤ U(E[W])

This is WHY risk aversion exists!
You prefer $100 for sure over a 50-50 bet of $0 or $200
even though E[bet] = $100.
```

**Application in AS Model**: Utility maximization leads to reservation price adjustment

---

## 1.12 TAIL RISK AND FAT TAILS

### Why Normal is Wrong for Finance

**The Normal assumption**:
- ~99.7% of returns within 3σ
- Extreme events (> 5σ) essentially impossible

**Reality - Famous "impossible" events**:
1. **Black Monday (Oct 1987)**: -22.6% in one day
   - If S&P 500 daily returns were Normal with σ=1%:
   - This is a **22.6σ event**
   - Probability: 10^(-109) (essentially zero!)
   - Should happen once every 10^100 years (universe is 10^10 years old)

2. **2008 Financial Crisis**: Multiple 10σ+ days

3. **Flash Crash (May 2010)**: Dow dropped 1000 points in minutes

**Conclusion**: Normal underestimates tail risk MASSIVELY

### Kurtosis

**Definition**:
```
Kurt(X) = E[(X - μ)⁴] / σ⁴
```

**Normal distribution**: Kurt = 3
**Excess kurtosis**: Kurt - 3

**Heavy tails**: Kurt > 3 (more extreme events than Normal)
**Light tails**: Kurt < 3 (fewer extreme events)

**Visual**:
```
Density
  │     Normal (Kurt=3)
  │       ╱╲
  │      ╱  ╲
  │     ╱    ╲___
  │    ╱         ╲___
  │───╱──────────────╲───
  │  Fat-tailed (Kurt>3)
  │       ╱╲
  │      ╱│╲______
  │     ╱ │ ╲     ╲____
  │    ╱  │  ╲         ╲___
  └───────┼────────────────► x
      Higher  Fatter
      peak    tails
```

### Example - Stock Returns

```python
import numpy as np
import scipy.stats as stats
import matplotlib.pyplot as plt

# Real S&P 500 daily returns (simulated with Student's t)
np.random.seed(42)
real_returns = np.random.standard_t(df=5, size=10000) * 0.01  # Fat tails
normal_returns = np.random.normal(0, 0.01, 10000)  # Gaussian

print("=== Kurtosis Comparison ===")
print(f"Real returns: Kurt = {stats.kurtosis(real_returns, fisher=False):.2f}")
print(f"Normal: Kurt = {stats.kurtosis(normal_returns, fisher=False):.2f}")

print("\n=== Extreme Event Frequency ===")
threshold_3sigma = 3 * 0.01
real_extreme = np.sum(np.abs(real_returns) > threshold_3sigma)
normal_extreme = np.sum(np.abs(normal_returns) > threshold_3sigma)
print(f"Real: {real_extreme} days beyond 3σ")
print(f"Normal (expected): {normal_extreme} days beyond 3σ")

# Plot
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))

ax1.hist(real_returns, bins=100, alpha=0.6, density=True, label='Real (t-dist)')
ax1.hist(normal_returns, bins=100, alpha=0.6, density=True, label='Normal')
ax1.set_xlim(-0.05, 0.05)
ax1.legend()
ax1.set_title("Return Distributions")

# QQ plot
stats.probplot(real_returns, dist="norm", plot=ax2)
ax2.set_title("Q-Q Plot (Real vs Normal)")
plt.tight_layout()
plt.show()
```

**Output**:
```
=== Kurtosis Comparison ===
Real returns: Kurt = 7.2  (fat tails!)
Normal: Kurt = 3.0

=== Extreme Event Frequency ===
Real: 87 days beyond 3σ
Normal (expected): 27 days beyond 3σ
```

### Why This Matters for Trading

**Risk models based on Normal distribution**:
- Underestimate Value-at-Risk (VaR)
- Underestimate probability of ruin
- Lead to under-hedging

**Example - VaR Calculation**:
```python
# Portfolio value $1M, daily σ = 2%
portfolio_value = 1_000_000
daily_std = 0.02

# 1-day 95% VaR (Normal assumption)
z_95 = 1.645  # 95th percentile of N(0,1)
VaR_normal = portfolio_value * z_95 * daily_std
print(f"VaR (Normal): ${VaR_normal:,.0f}")

# But if returns are t-distributed (fat tails):
t_95 = stats.t.ppf(0.95, df=5)  # df=5 for heavy tails
VaR_realistic = portfolio_value * t_95 * daily_std
print(f"VaR (Fat-tailed): ${VaR_realistic:,.0f}")

print(f"\nUnderestimation: {VaR_realistic / VaR_normal:.1f}x")
```

**Output**:
```
VaR (Normal): $32,900
VaR (Fat-tailed): $40,200

Underestimation: 1.2x
```

### Solutions

1. **Use t-distribution** instead of Normal
   - Heavier tails
   - Controlled by degrees of freedom parameter

2. **GARCH models** (volatility clustering)
   - σ_t changes over time
   - High-vol periods follow high-vol periods

3. **Extreme Value Theory** (EVT)
   - Model tail separately
   - Generalized Pareto Distribution for exceedances

4. **Copulas**
   - Model dependence structure in tails
   - Capture tail dependence (assets crash together)

### For Your Market Maker

**Implications**:
1. **Widen spreads** during high-volatility periods
2. **Monitor tail risk metrics** (not just σ)
3. **Stress test** with extreme scenarios
4. **Dynamic risk limits** based on regime (calm vs volatile)

**In Avellaneda-Stoikov**:
- σ parameter should be estimated with fat tails in mind
- Consider regime-switching: different σ in calm vs volatile markets
- Add tail-risk premium to spread

---

## 1.13 SUMMARY AND NEXT STEPS

**What you now understand**:
✅ Probability axioms and conditional probability
✅ Random variables, PMF, PDF
✅ Expectation, variance, covariance, correlation
✅ Normal, log-normal, exponential distributions
✅ LLN and CLT (why averages converge)
✅ MGF and conditional expectation
✅ Jensen's inequality (utility and risk aversion)
✅ Fat tails and kurtosis

**Next Chapter**: Multivariate distributions, covariance matrices, PCA, and portfolio theory

**Practice Problems**:

1. **Compute**: If X ~ N(0, 1), find E[X²] and E[X⁴]
2. **Prove**: If X and Y are independent, then Cov(X, Y) = 0
3. **Simulate**: Generate 10,000 samples from exponential(λ=0.5) and verify E[X] = 1/λ
4. **Trading**: Given daily returns of 2%, -1%, 3%, -2%, 1%, compute mean, variance, and 95% confidence interval
