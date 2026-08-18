# MATHEMATICS QUICK REFERENCE FOR QUANT FINANCE

**All key formulas in one place—your cheat sheet during implementation!**

---

## PROBABILITY & STATISTICS

### Basic Formulas
```
E[aX + b] = aE[X] + b
Var(aX + b) = a²Var(X)
Var(X) = E[X²] - (E[X])²
Cov(X, Y) = E[XY] - E[X]E[Y]
ρ(X, Y) = Cov(X, Y) / (σ_X σ_Y)
```

### Normal Distribution
```
X ~ N(μ, σ²)
PDF: f(x) = (1/√(2πσ²)) exp(-(x-μ)²/(2σ²))
E[X] = μ, Var(X) = σ²
68% within μ ± σ
95% within μ ± 2σ
```

### Portfolio Variance
```
σ_p² = wᵀΣw = Σᵢ Σⱼ wᵢwⱼσᵢⱼ

Two assets:
σ_p² = w₁²σ₁² + w₂²σ₂² + 2w₁w₂ρ₁₂σ₁σ₂
```

---

## CALCULUS

### Derivatives
```
d/dx(x^n) = nx^{n-1}
d/dx(e^x) = e^x
d/dx(ln x) = 1/x
d/dx(sin x) = cos x
d/dx(cos x) = -sin x

Chain rule: d/dx f(g(x)) = f'(g(x))·g'(x)
Product rule: d/dx(fg) = f'g + fg'
```

### Taylor Series
```
f(x+h) ≈ f(x) + f'(x)h + ½f''(x)h²

e^x = 1 + x + x²/2! + x³/3! + ...
ln(1+x) = x - x²/2 + x³/3 - ...
sin(x) = x - x³/3! + x⁵/5! - ...
```

### Partial Derivatives
```
Gradient: ∇f = [∂f/∂x₁, ..., ∂f/∂xₙ]ᵀ
Hessian: H = [∂²f/∂xᵢ∂xⱼ]

Chain rule: dz/dt = ∂z/∂x·dx/dt + ∂z/∂y·dy/dt
```

---

## STOCHASTIC CALCULUS

### Brownian Motion
```
W(t) ~ N(0, t)
E[W(t)] = 0
Var(W(t)) = t
Cov(W(s), W(t)) = min(s, t)

Key rule: (dW)² = dt
```

### Itô's Lemma
```
If dX = μ dt + σ dW
and f(X, t) is smooth:

df = (∂f/∂t + μ·∂f/∂X + ½σ²·∂²f/∂X²) dt + σ·∂f/∂X dW
```

### Multiplication Rules
```
dt · dt = 0
dt · dW = 0
dW · dW = dt  ← KEY!
```

### Geometric Brownian Motion
```
dS = μS dt + σS dW

Solution:
S(t) = S₀ exp((μ - σ²/2)t + σW(t))
```

---

## AVELLANEDA-STOIKOV MODEL

### Reservation Price
```
r_t = s_t - q_t · γ · σ² · (T - t)
```
- s_t: Mid-price
- q_t: Inventory (+ long, - short)
- γ: Risk aversion
- σ: Volatility
- T-t: Time remaining

### Optimal Spread
```
δ* = γσ²(T-t) + (2/γ)ln(1 + γ/κ)
```
- First term: Risk premium
- Second term: Liquidity premium
- κ: Fill rate parameter

### Quotes
```
bid = r - δ*/2
ask = r + δ*/2
```

---

## ALMGREN-CHRISS MODEL

### Optimal Trajectory
```
x(t) = X · sinh(κ(T-t)) / sinh(κT)

where κ = √(λσ²/η)
```
- X: Total shares to execute
- λ: Risk aversion
- σ: Volatility
- η: Temporary impact coefficient

### Trade Schedule
```
Trades[i] = x(t_{i-1}) - x(t_i)
```

---

## HAWKES PROCESS

### Intensity
```
λ(t) = μ + Σ_{t_i < t} α·exp(-β(t - t_i))
```
- μ: Baseline intensity
- α: Excitation (jump size)
- β: Decay rate

### Stability
```
α < β  (required for convergence)
```

### Steady-State Intensity
```
κ = μ / (1 - α/β)
```

### Log-Likelihood
```
LL = Σᵢ ln(λ(tᵢ)) - ∫₀ᵀ λ(t) dt
   = Σᵢ ln(λ(tᵢ)) - μT - (α/β)Σᵢ(1 - e^{-β(T-tᵢ)})
```

---

## OPTION GREEKS

### Delta (Price Sensitivity)
```
Δ = ∂C/∂S
```

### Gamma (Convexity)
```
Γ = ∂²C/∂S² = ∂Δ/∂S
```

### Theta (Time Decay)
```
Θ = ∂C/∂t
```

### Vega (Vol Sensitivity)
```
ν = ∂C/∂σ
```

### Black-Scholes Call
```
C = S·N(d₁) - K·e^{-r(T-t)}·N(d₂)

d₁ = (ln(S/K) + (r + σ²/2)(T-t)) / (σ√(T-t))
d₂ = d₁ - σ√(T-t)
```

---

## RETURNS

### Simple Return
```
R_t = (S_t - S_{t-1}) / S_{t-1}
```

### Log Return
```
r_t = ln(S_t / S_{t-1}) = ln(S_t) - ln(S_{t-1})
```

### Properties
```
log returns are time-additive:
r(0→T) = r(0→t) + r(t→T)

For small R: r ≈ R
```

---

## RISK METRICS

### Value at Risk (VaR)
```
VaR_α = -quantile(returns, α)
```
Example: VaR₀.₀₅ = 5th percentile loss

### Expected Shortfall (ES)
```
ES_α = E[Loss | Loss > VaR_α]
```
Average loss beyond VaR

### Sharpe Ratio
```
Sharpe = (E[R_p] - R_f) / σ_p
```

### Maximum Drawdown
```
MDD = max_t (peak_t - value_t) / peak_t
```

---

## COMMON DISTRIBUTIONS

### Exponential
```
f(t) = λe^{-λt}
E[T] = 1/λ
Var(T) = 1/λ²
```

### Log-Normal
```
If X ~ N(μ, σ²), then Y = e^X ~ LogNormal
E[Y] = exp(μ + σ²/2)
```

### Student's t
```
Heavier tails than Normal
df = degrees of freedom
```

---

## NUMERICAL METHODS

### Euler-Maruyama (SDE Simulation)
```
X_{n+1} = X_n + μ(X_n, t_n)Δt + σ(X_n, t_n)ΔW_n

where ΔW_n ~ N(0, Δt)
```

### Monte Carlo Option Pricing
```
C ≈ e^{-rT} · (1/N) Σᵢ payoff(S_T^(i))
```

### EWMA Volatility
```
variance_t = λ·variance_{t-1} + (1-λ)·r_t²
volatility_t = √variance_t
```

---

## OPTIMIZATION

### Lagrange Multipliers
```
minimize f(x)
subject to g(x) = 0

L(x, λ) = f(x) + λg(x)
FOC: ∇L = 0
```

### Minimum Variance Portfolio
```
w* = Σ⁻¹·1 / (1ᵀΣ⁻¹·1)
```

### Maximum Sharpe Portfolio
```
w* ∝ Σ⁻¹(μ - R_f·1)
```

---

## USEFUL INEQUALITIES

### Jensen's Inequality
```
If φ is convex: φ(E[X]) ≤ E[φ(X)]
If φ is concave: φ(E[X]) ≥ E[φ(X)]
```

### Cauchy-Schwarz
```
|E[XY]| ≤ √(E[X²]E[Y²])
```

### Markov's Inequality
```
P(X ≥ a) ≤ E[X]/a  for X ≥ 0, a > 0
```

### Chebyshev's Inequality
```
P(|X - μ| ≥ kσ) ≤ 1/k²
```

---

## MATRIX OPERATIONS

### Covariance Matrix
```
Σ = E[(X - μ)(X - μ)ᵀ]
```

### Eigendecomposition
```
Σ = QΛQᵀ
Q = eigenvectors (principal components)
Λ = diagonal of eigenvalues
```

### Matrix Calculus
```
∂(xᵀAx)/∂x = (A + Aᵀ)x = 2Ax  (if A symmetric)
∂(Ax)/∂x = Aᵀ
```

---

## CONVERSION FACTORS

### Time
```
1 year = 252 trading days
1 day = 390 minutes (US market: 9:30am - 4:00pm)
1 minute = 60 seconds
```

### Volatility Scaling
```
σ_annual = σ_daily · √252
σ_daily = σ_minute · √390
```

### Tick Size
```
BTC/USDT: $0.01 (1 cent)
Tick representation: price * 100 (integer)
Example: $50,025.50 → 5,002,550 ticks
```

---

## TYPICAL PARAMETER VALUES

### Avellaneda-Stoikov
```
γ (risk aversion): 0.01 - 0.5
κ (fill rate): 0.5 - 5.0
σ (volatility): 0.001 - 0.05 per √minute
T (horizon): 100 - 1000 ticks (~10-100 minutes)
```

### Almgren-Chriss
```
λ (risk aversion): 1e-6 - 1e-4
η (temporary impact): 1e-8 - 1e-6
γ (permanent impact): 1e-9 - 1e-7
```

### Hawkes
```
μ (baseline): 0.1 - 10 events/second
α (excitation): 0.1 - 0.9
β (decay): 0.5 - 5.0
Constraint: α < β
```

### EWMA
```
λ (decay): 0.94 (daily), 0.98 (high-freq)
```

---

**This is your go-to reference while coding. Keep it open in a second monitor!**

**For derivations and intuition, see the full chapters:**
- MATH_01: Probability foundations
- MATH_02: Multivariate stats & portfolio theory
- MATH_03: Calculus foundations
- MATH_04: Stochastic calculus (Itô's lemma, SDEs)
- MATH_05: Complete model derivations (AS, AC, Hawkes)
