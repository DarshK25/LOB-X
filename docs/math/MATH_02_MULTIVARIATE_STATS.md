# CHAPTER 2: MULTIVARIATE STATISTICS & PORTFOLIO THEORY

**Goal**: Understand joint distributions, covariance matrices, and how multiple assets interact—essential for portfolio optimization and risk management.

---

## 2.1 JOINT PROBABILITY DISTRIBUTIONS

### Joint PDF/PMF

**Two continuous random variables X and Y**:
```
Joint PDF: f(x, y)
P(a < X < b, c < Y < d) = ∫∫ f(x,y) dy dx
```

**Properties**:
1. f(x, y) ≥ 0
2. ∫∫ f(x,y) dy dx = 1

### Marginal Distributions

**How to get individual distributions from joint**:
```
f_X(x) = ∫ f(x,y) dy   (integrate out y)
f_Y(y) = ∫ f(x,y) dx   (integrate out x)
```

**Example - Portfolio**:
```python
# Joint distribution of BTC and ETH returns
# Marginal: Distribution of BTC returns alone
```

### Independence (Multivariate)

**X and Y are independent if**:
```
f(x, y) = f_X(x) · f_Y(y)  for all x, y
```

**Consequence**: If independent, then:
- E[XY] = E[X]·E[Y]
- Cov(X, Y) = 0
- Knowing X tells you NOTHING about Y

**Counter-example**: Cov(X, Y) = 0 does NOT imply independence!

---

## 2.2 COVARIANCE MATRIX

### Definition (n assets)

**For random vector X = [X₁, X₂, ..., Xₙ]ᵀ**:

```
Σ = E[(X - μ)(X - μ)ᵀ]

where μ = E[X] = [E[X₁], E[X₂], ..., E[Xₙ]]ᵀ
```

**Matrix form**:
```
Σ = [
  σ₁²      σ₁₂     ...  σ₁ₙ
  σ₂₁      σ₂²     ...  σ₂ₙ
  ...      ...     ...  ...
  σₙ₁      σₙ₂     ...  σₙ²
]

where σᵢⱼ = Cov(Xᵢ, Xⱼ)
```

### Properties

1. **Symmetric**: Σ = Σᵀ  (since Cov(Xᵢ, Xⱼ) = Cov(Xⱼ, Xᵢ))
2. **Positive semi-definite**: xᵀΣx ≥ 0 for all x
3. **Diagonal elements**: Var(Xᵢ) = σᵢ²

### Example - 3 Assets

```python
import numpy as np

# Daily returns for BTC, ETH, SOL
returns = np.array([
    [0.02, -0.01, 0.03],  # Day 1
    [-0.01, 0.02, -0.02], # Day 2
    [0.03, 0.01, 0.04],   # Day 3
    [-0.02, -0.01, 0.00], # Day 4
    [0.01, 0.03, 0.02],   # Day 5
])

# Covariance matrix
cov_matrix = np.cov(returns.T)  # Transpose: columns = variables
print("Covariance Matrix:")
print(cov_matrix)

# Correlation matrix (normalized)
corr_matrix = np.corrcoef(returns.T)
print("\nCorrelation Matrix:")
print(corr_matrix)
```

---

## 2.3 MULTIVARIATE NORMAL DISTRIBUTION

### Definition

**X ~ N(μ, Σ)** if it has joint PDF:

```
f(x) = (1 / √((2π)ⁿ |Σ|)) · exp(-½(x-μ)ᵀ Σ⁻¹ (x-μ))
```

where:
- μ = mean vector (n × 1)
- Σ = covariance matrix (n × n)
- |Σ| = determinant of Σ

### Properties (CRITICAL!)

1. **Marginals are Normal**: If X ~ N(μ, Σ), then Xᵢ ~ N(μᵢ, σᵢ²)

2. **Linear combinations are Normal**:
   ```
   If X ~ N(μ, Σ), then aᵀX ~ N(aᵀμ, aᵀΣa)
   ```

3. **Uncorrelated ⟹ Independent** (ONLY for Normal!)
   ```
   If X ~ N(μ, Σ) and Σ is diagonal, then X₁, ..., Xₙ are independent
   ```

4. **Conditional distributions are Normal**:
   ```
   X₁|X₂ ~ N(μ₁ + Σ₁₂Σ₂₂⁻¹(X₂ - μ₂), Σ₁₁ - Σ₁₂Σ₂₂⁻¹Σ₂₁)
   ```

### Why Critical in Finance?

- Returns are often modeled as multivariate Normal
- Portfolio returns = linear combination of asset returns
- Brownian motion → multivariate Normal increments

---

## 2.4 PORTFOLIO VARIANCE

### Two Assets

**Portfolio**: w₁ invested in asset 1, w₂ in asset 2 (w₁ + w₂ = 1)

**Return**:
```
R_p = w₁R₁ + w₂R₂
```

**Expected return**:
```
E[R_p] = w₁E[R₁] + w₂E[R₂]  (linearity)
```

**Variance** (KEY FORMULA):
```
Var(R_p) = w₁²σ₁² + w₂²σ₂² + 2w₁w₂σ₁₂

where σ₁₂ = Cov(R₁, R₂)
```

**Alternative form** (using correlation ρ₁₂):
```
Var(R_p) = w₁²σ₁² + w₂²σ₂² + 2w₁w₂ρ₁₂σ₁σ₂
```

### n Assets (Matrix Form)

**Portfolio weights**: w = [w₁, w₂, ..., wₙ]ᵀ with Σwᵢ = 1

**Portfolio variance**:
```
σ_p² = wᵀΣw

where Σ = covariance matrix
```

**Expanded**:
```
σ_p² = Σᵢ Σⱼ wᵢwⱼσᵢⱼ
```

### Example

```python
import numpy as np

# 3 assets: BTC, ETH, SOL
returns = np.array([[0.02, 0.01, 0.03],
                    [-0.01, 0.02, -0.02],
                    [0.03, 0.01, 0.04],
                    [-0.02, -0.01, 0.00],
                    [0.01, 0.03, 0.02]])

# Covariance matrix
Sigma = np.cov(returns.T)

# Portfolio weights
w = np.array([0.5, 0.3, 0.2])  # 50% BTC, 30% ETH, 20% SOL

# Portfolio variance
portfolio_var = w.T @ Sigma @ w
portfolio_std = np.sqrt(portfolio_var)

print(f"Portfolio variance: {portfolio_var:.6f}")
print(f"Portfolio std dev: {portfolio_std:.6f}")

# Expected return
mean_returns = np.mean(returns, axis=0)
expected_return = w @ mean_returns
print(f"Expected return: {expected_return:.4f}")
```

---

## 2.5 DIVERSIFICATION BENEFIT

### Key Insight

**If ρ₁₂ < 1, then portfolio variance < weighted average of individual variances!**

### Equal-Weighted Portfolio

**n assets, equal weights wᵢ = 1/n, all σᵢ = σ, all ρᵢⱼ = ρ**:

```
σ_p² = (1/n)σ² + (1 - 1/n)ρσ²
     = σ²/n + ρσ²(n-1)/n
```

**As n → ∞**:
```
σ_p² → ρσ²  (systematic risk, cannot be diversified away)
```

**Diversifiable risk**: σ²/n → 0

### Visualization

```
Portfolio Risk
σ_p
  │
  │╲
  │ ╲____________  ρσ² (systematic risk)
  │  ╲
  │   ╲_
  │     ╲___
  └──────────────► n (number of assets)
```

### Python Demo

```python
import numpy as np
import matplotlib.pyplot as plt

sigma = 0.20  # 20% individual asset vol
rho = 0.3     # 30% correlation

n_assets = range(1, 101)
portfolio_vols = []

for n in n_assets:
    sigma_p = np.sqrt(sigma**2 / n + rho * sigma**2 * (n-1) / n)
    portfolio_vols.append(sigma_p)

plt.plot(n_assets, portfolio_vols)
plt.axhline(y=np.sqrt(rho)*sigma, color='r', linestyle='--', label='Systematic risk')
plt.xlabel("Number of Assets")
plt.ylabel("Portfolio Volatility")
plt.title("Diversification Benefit")
plt.legend()
plt.grid(True)
plt.show()
```

---

## 2.6 CORRELATION AND DEPENDENCE

### Correlation Coefficient

```
ρ(X, Y) = Cov(X, Y) / (σ_X σ_Y)
```

**Properties**:
1. -1 ≤ ρ ≤ 1
2. ρ = ±1 ⟺ perfect linear relationship
3. ρ = 0 ⟺ uncorrelated (but NOT necessarily independent!)

### Example: Uncorrelated but Dependent

```python
import numpy as np
import matplotlib.pyplot as plt

# X ~ Uniform[-1, 1]
X = np.random.uniform(-1, 1, 10000)
# Y = X² (deterministic function of X!)
Y = X**2

# Correlation
print(f"Correlation: {np.corrcoef(X, Y)[0,1]:.4f}")  # ≈ 0 !

# But clearly dependent
plt.scatter(X, Y, alpha=0.3, s=1)
plt.xlabel("X")
plt.ylabel("Y = X²")
plt.title("Uncorrelated but Dependent")
plt.show()
```

### Copulas (Advanced Topic Preview)

**Copulas** capture dependence structure beyond correlation.

Example: Tail dependence (assets crash together even if ρ is moderate)

---

## 2.7 PRINCIPAL COMPONENT ANALYSIS (PCA)

### Goal

**Reduce dimensionality while preserving variance**

### Mathematical Foundation

**Given**: Data matrix X (n × p), n observations, p variables

**Steps**:
1. Center data: X̃ = X - mean
2. Compute covariance matrix: Σ = (1/n) X̃ᵀX̃
3. Eigen-decomposition: Σ = QΛQᵀ
   - Q = eigenvectors (principal components)
   - Λ = diagonal matrix of eigenvalues (variances)
4. Project: Z = X̃Q

**Interpretation**:
- PC1 = direction of maximum variance
- PC2 = direction of maximum variance orthogonal to PC1
- etc.

### Explained Variance

```
Variance explained by PCᵢ = λᵢ / Σλⱼ
```

### Trading Example: Factor Models

```python
import numpy as np
from sklearn.decomposition import PCA
import matplotlib.pyplot as plt

# Daily returns for 10 stocks (simulated)
np.random.seed(42)
n_days = 252
n_stocks = 10

# Market factor (systematic)
market = np.random.normal(0, 0.01, n_days)
# Idiosyncratic noise
noise = np.random.normal(0, 0.005, (n_days, n_stocks))
# Stock returns = beta * market + noise
betas = np.random.uniform(0.5, 1.5, n_stocks)
returns = market[:, np.newaxis] * betas + noise

# PCA
pca = PCA()
pca.fit(returns)

# Explained variance
explained_var = pca.explained_variance_ratio_
print(f"PC1 explains: {explained_var[0]:.2%}")
print(f"PC2 explains: {explained_var[1]:.2%}")

# Scree plot
plt.bar(range(1, 11), explained_var)
plt.xlabel("Principal Component")
plt.ylabel("Variance Explained")
plt.title("PCA Scree Plot")
plt.show()

# First PC ~ market factor!
print(f"\nPC1 loadings (should be similar across stocks):")
print(pca.components_[0])
```

**Output**: PC1 captures ~70% of variance (the market factor)

---

## 2.8 PAIR TRADING AND MEAN REVERSION

### Concept

**Find two assets with high correlation**:
1. Compute spread: S_t = P₁_t - β·P₂_t
2. If spread is "wide" (S_t > mean + k·σ), short spread (sell 1, buy 2)
3. If spread is "narrow" (S_t < mean - k·σ), long spread (buy 1, sell 2)
4. When spread reverts to mean, close position

### Cointegration (Key Math)

**Two time series P₁_t and P₂_t are cointegrated if**:
```
S_t = P₁_t - β·P₂_t  is stationary

where stationary means:
- E[S_t] = constant
- Var(S_t) = constant
- Cov(S_t, S_{t+k}) depends only on k, not t
```

**Tests**: Augmented Dickey-Fuller (ADF) test

### Example

```python
import numpy as np
import matplotlib.pyplot as plt
from statsmodels.tsa.stattools import adfuller

# Simulate cointegrated pair
np.random.seed(42)
n = 252

# Common trend
trend = np.cumsum(np.random.normal(0, 1, n))
# Add noise
P1 = trend + np.random.normal(0, 0.5, n)
P2 = 1.5 * trend + np.random.normal(0, 0.5, n)

# Spread
beta = 1.5
spread = P1 - beta * P2

# ADF test (null hypothesis: non-stationary)
adf_result = adfuller(spread)
print(f"ADF statistic: {adf_result[0]:.4f}")
print(f"p-value: {adf_result[1]:.4f}")
if adf_result[1] < 0.05:
    print("Spread is stationary (cointegrated)!")

# Plot
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6))
ax1.plot(P1, label='Asset 1')
ax1.plot(P2, label='Asset 2')
ax1.legend()
ax1.set_title("Cointegrated Assets")

ax2.plot(spread)
ax2.axhline(spread.mean(), color='r', linestyle='--')
ax2.axhline(spread.mean() + 2*spread.std(), color='g', linestyle='--')
ax2.axhline(spread.mean() - 2*spread.std(), color='g', linestyle='--')
ax2.set_title("Spread (Mean-Reverting)")
plt.show()
```

---

## 2.9 MARKOWITZ PORTFOLIO THEORY

### Mean-Variance Optimization

**Goal**: Minimize risk for given return (or maximize return for given risk)

**Formulation**:
```
minimize    wᵀΣw              (portfolio variance)
subject to  wᵀμ = μ_target    (target return)
           Σwᵢ = 1             (fully invested)
```

**Lagrangian**:
```
L = wᵀΣw + λ(μ_target - wᵀμ) + γ(1 - Σwᵢ)
```

**Solution** (efficient frontier):
```
w* = (1/γ)Σ⁻¹(μ + λ·1)

where λ is chosen to satisfy constraints
```

### Efficient Frontier

**Plot**: Risk (σ_p) vs Return (μ_p) for all optimal portfolios

```
   μ_p (return)
    │
    │      ╱─
    │    ╱
    │  ╱      ← Efficient Frontier
    │╱___
    └──────────► σ_p (risk)
```

### Python Implementation

```python
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import minimize

# 3 assets
mean_returns = np.array([0.10, 0.12, 0.15])
cov_matrix = np.array([
    [0.04, 0.01, 0.02],
    [0.01, 0.06, 0.03],
    [0.02, 0.03, 0.09]
])

def portfolio_stats(weights):
    ret = weights @ mean_returns
    vol = np.sqrt(weights @ cov_matrix @ weights)
    return ret, vol

# Efficient frontier
target_returns = np.linspace(0.10, 0.15, 50)
efficient_vols = []

for target in target_returns:
    constraints = [
        {'type': 'eq', 'fun': lambda w: np.sum(w) - 1},
        {'type': 'eq', 'fun': lambda w: w @ mean_returns - target}
    ]
    bounds = tuple((0, 1) for _ in range(3))
    result = minimize(
        lambda w: w @ cov_matrix @ w,
        x0=[1/3, 1/3, 1/3],
        constraints=constraints,
        bounds=bounds
    )
    efficient_vols.append(np.sqrt(result.fun))

plt.plot(efficient_vols, target_returns, 'b-', label='Efficient Frontier')
plt.xlabel("Portfolio Volatility")
plt.ylabel("Portfolio Return")
plt.title("Markowitz Efficient Frontier")
plt.legend()
plt.grid(True)
plt.show()
```

---

## 2.10 SHARPE RATIO

### Definition

```
Sharpe Ratio = (E[R_p] - R_f) / σ_p

where:
- R_p = portfolio return
- R_f = risk-free rate
- σ_p = portfolio volatility
```

**Interpretation**: Return per unit of risk

**Goal**: Maximize Sharpe ratio

### Maximum Sharpe Portfolio

**Tangency portfolio** (on efficient frontier):

```
w* ∝ Σ⁻¹(μ - R_f·1)
```

Normalize: Σwᵢ = 1

---

## 2.11 SUMMARY

**What you now understand**:
✅ Joint distributions and marginals
✅ Covariance matrices and multivariate Normal
✅ Portfolio variance formula: wᵀΣw
✅ Diversification benefit
✅ PCA for dimension reduction
✅ Cointegration and pair trading
✅ Markowitz mean-variance optimization
✅ Efficient frontier and Sharpe ratio

**Next Chapter**: Calculus refresher → Taylor series → Itô's lemma foundation

**Practice Problems**:

1. **Compute**: Portfolio variance for w = [0.6, 0.4], σ₁ = 0.2, σ₂ = 0.3, ρ = 0.5
2. **Prove**: wᵀΣw ≥ 0 for all w (covariance matrix is PSD)
3. **Code**: Implement PCA on real stock data, plot first 2 principal components
4. **Optimize**: Find minimum variance portfolio for 3 assets with given Σ

