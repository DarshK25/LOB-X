# CHAPTER 3: CALCULUS FOUNDATIONS FOR DERIVATIVES

**Goal**: Master differentiation, integration, Taylor series, and partial derivatives—essential for option pricing and optimal control.

---

## 3.1 LIMITS (Quick Review)

### Definition

```
lim (x→a) f(x) = L

means: f(x) gets arbitrarily close to L as x approaches a
```

### Important Limits

```
1. lim (x→0) sin(x)/x = 1
2. lim (x→∞) (1 + 1/x)^x = e ≈ 2.71828...
3. lim (h→0) (e^h - 1)/h = 1
4. lim (h→0) (ln(1+h))/h = 1
```

---

## 3.2 DERIVATIVES - COMPLETE FOUNDATION

### Definition (From First Principles)

```
f'(x) = df/dx = lim (h→0) [f(x+h) - f(x)] / h
```

**Interpretation**: Instantaneous rate of change

**Geometric meaning**: Slope of tangent line at point x

**Example - Derive derivative of x²**:
```
f(x) = x²
f(x+h) = (x+h)² = x² + 2xh + h²

f'(x) = lim(h→0) [(x² + 2xh + h²) - x²] / h
      = lim(h→0) [2xh + h²] / h
      = lim(h→0) [2x + h]
      = 2x
```

### Basic Derivative Rules

**1. Constant rule**: d/dx(c) = 0

**2. Power rule**: d/dx(x^n) = nx^(n-1)

**Proof**:
```
d/dx(x^n) = lim(h→0) [(x+h)^n - x^n] / h

Using binomial theorem: (x+h)^n = x^n + nx^(n-1)h + O(h²)

= lim(h→0) [nx^(n-1)h + O(h²)] / h
= nx^(n-1)
```

**3. Sum rule**: d/dx(f + g) = f' + g'

**4. Constant multiple**: d/dx(cf) = c·f'

**5. Product rule**: d/dx(f·g) = f'·g + f·g'

**Proof** (geometric intuition):
```
Area of rectangle = f(x) · g(x)

When x changes by h:
New area = (f + f'h)(g + g'h)
         = fg + f·g'h + g·f'h + f'g'h²

Change in area = f·g'h + g·f'h + O(h²)
Rate of change = f·g' + g·f'
```

**6. Quotient rule**: d/dx(f/g) = (f'g - fg')/g²

**Mnemonic**: "Low dee-high minus high dee-low, square the bottom and away we go"

**7. Chain rule**: d/dx f(g(x)) = f'(g(x)) · g'(x)

**Proof sketch**:
```
Δy = f(g(x+h)) - f(g(x))
   = f(g+Δg) - f(g)
   = f'(g)·Δg  (first-order approximation)

Δy/Δx = f'(g) · Δg/Δx

As Δx→0: dy/dx = f'(g) · dg/dx
```

### Complete Table of Derivatives

**Polynomials**:
```
d/dx(x^n) = nx^(n-1)
d/dx(1/x) = -1/x²
d/dx(√x) = 1/(2√x)
d/dx(x^(1/n)) = (1/n)x^((1/n)-1)
```

**Exponential functions**:
```
d/dx(e^x) = e^x
d/dx(a^x) = a^x · ln(a)
d/dx(e^(kx)) = k·e^(kx)  (chain rule)
```

**Logarithmic functions**:
```
d/dx(ln x) = 1/x
d/dx(log_a x) = 1/(x·ln a)
d/dx(ln|x|) = 1/x  (works for x < 0 too)
d/dx(ln(f(x))) = f'(x)/f(x)  (chain rule)
```

**Trigonometric functions**:
```
d/dx(sin x) = cos x
d/dx(cos x) = -sin x
d/dx(tan x) = sec² x = 1/cos² x
d/dx(cot x) = -csc² x = -1/sin² x
d/dx(sec x) = sec x · tan x
d/dx(csc x) = -csc x · cot x
```

**Inverse trig functions**:
```
d/dx(arcsin x) = 1/√(1-x²)
d/dx(arccos x) = -1/√(1-x²)
d/dx(arctan x) = 1/(1+x²)
```

**Hyperbolic functions**:
```
d/dx(sinh x) = cosh x
d/dx(cosh x) = sinh x
d/dx(tanh x) = sech² x
```

### Financial Applications of Derivatives

#### 1. Option Delta (Sensitivity to Price)

**Call option value**: C(S, t)

**Delta**: Δ = ∂C/∂S

**Physical meaning**: 
- If stock moves $1, option moves $Δ
- Δ ∈ [0, 1] for calls
- Δ ∈ [-1, 0] for puts

**Example**:
```python
# Black-Scholes delta for call
def call_delta(S, K, T, r, sigma):
    d1 = (np.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*np.sqrt(T))
    return norm.cdf(d1)

S = 100
K = 100
delta = call_delta(S, K, 1.0, 0.05, 0.20)
print(f"Delta = {delta:.4f}")  # ≈ 0.5637

# If stock moves from $100 to $101:
# Option value changes by approximately $0.5637
```

#### 2. Duration (Bond Price Sensitivity)

**Bond price**: P(y) where y = yield

**Duration**: D = -(1/P)·dP/dy

**Physical meaning**: % change in price per 1% change in yield

**Example**:
```python
# 10-year bond, 5% coupon, 5% yield
def bond_price(y, coupon=0.05, face=100, T=10):
    return sum(coupon*face / (1+y)**t for t in range(1, T+1)) + face/(1+y)**T

y = 0.05
P = bond_price(y)
dP_dy = (bond_price(y+0.0001) - bond_price(y-0.0001)) / 0.0002  # Numerical
duration = -(1/P) * dP_dy
print(f"Duration = {duration:.2f} years")  # ≈ 7.72

# If yield increases from 5% to 6%:
# Bond price drops by approximately 7.72%
```

#### 3. Marginal Cost/Revenue

**Cost function**: C(q) = cost to produce q units

**Marginal cost**: MC = dC/dq (cost of one more unit)

**Profit maximization**: Set MR = MC (marginal revenue = marginal cost)

#### 4. Elasticity

**Price elasticity of demand**: ε = (dQ/dP) · (P/Q)

**Interpretation**: % change in quantity per 1% change in price

---

## 3.2B TRIGONOMETRY ESSENTIALS

**Why in a finance book?** 
- Fourier analysis (decompose price signals into cycles)
- Option pricing with characteristic functions
- Seasonal patterns in commodities

### Fundamental Identities

**Pythagorean identities**:
```
sin² x + cos² x = 1
1 + tan² x = sec² x
1 + cot² x = csc² x
```

**Sum/Difference formulas**:
```
sin(A ± B) = sin A cos B ± cos A sin B
cos(A ± B) = cos A cos B ∓ sin A sin B
tan(A ± B) = (tan A ± tan B) / (1 ∓ tan A tan B)
```

**Double angle formulas**:
```
sin(2x) = 2 sin x cos x
cos(2x) = cos² x - sin² x = 2cos² x - 1 = 1 - 2sin² x
tan(2x) = 2 tan x / (1 - tan² x)
```

**Half angle formulas**:
```
sin²(x/2) = (1 - cos x) / 2
cos²(x/2) = (1 + cos x) / 2
```

**Product-to-sum**:
```
sin A cos B = [sin(A+B) + sin(A-B)] / 2
cos A cos B = [cos(A-B) + cos(A+B)] / 2
sin A sin B = [cos(A-B) - cos(A+B)] / 2
```

### Small Angle Approximations (CRITICAL!)

**For x near 0** (in radians):
```
sin x ≈ x
cos x ≈ 1 - x²/2
tan x ≈ x
```

**Example - Option pricing approximation**:
```python
# Black-Scholes for near-the-money options (S ≈ K)
# Uses small-angle approximations extensively
```

---

## 3.2C LOGARITHM PROPERTIES (Complete Reference)

**Definition**: y = log_a(x) means a^y = x

**Natural log**: ln(x) = log_e(x) where e ≈ 2.71828...

### All Logarithm Rules

```
ln(ab) = ln(a) + ln(b)
ln(a/b) = ln(a) - ln(b)
ln(a^b) = b·ln(a)
ln(1) = 0
ln(e) = 1
ln(1/a) = -ln(a)

Change of base: log_a(x) = ln(x) / ln(a)
```

### Why Logarithms in Finance?

**1. Returns are additive in log space**:
```
R_total = R₁ + R₂ + ... + Rₙ  (simple returns multiply)
r_total = r₁ + r₂ + ... + rₙ  (log returns add!)
```

**Example**:
```python
prices = [100, 105, 102, 108]

# Simple returns (multiply)
simple_rets = np.diff(prices) / prices[:-1]
total_simple = np.prod(1 + simple_rets) - 1
print(f"Total simple return: {total_simple:.4f}")  # 8.00%

# Log returns (add)
log_rets = np.diff(np.log(prices))
total_log = np.sum(log_rets)
print(f"Total log return: {total_log:.4f}")  # 7.70%
print(f"Via exp: {np.exp(total_log) - 1:.4f}")  # Converts back: 8.00%
```

**2. Log-normality of prices**:
```
If returns ~ Normal, then prices ~ Log-Normal
S_t = S_0 · exp(cumulative log returns)
```

**3. Continuously compounded rates**:
```
FV = PV · e^(rt)  (continuous compounding)
r = ln(FV/PV) / t  (continuously compounded rate)
```

### Exponential Properties

```
e^(a+b) = e^a · e^b
e^(a-b) = e^a / e^b
(e^a)^b = e^(ab)
e^0 = 1
e^(-x) = 1/e^x
```

### Connection to Derivatives

```
d/dx(ln x) = 1/x  →  ∫ 1/x dx = ln|x| + C
d/dx(e^x) = e^x   →  ∫ e^x dx = e^x + C
```

**Finance application - Continuous return**:
```python
# Stock price today: S_0 = 100
# Stock price in 1 year: S_1 = 110
# What's the continuously compounded return?

S_0 = 100
S_1 = 110
r_continuous = np.log(S_1 / S_0)
print(f"Continuous return: {r_continuous:.4f} = {r_continuous*100:.2f}%")

# Verify
S_1_check = S_0 * np.exp(r_continuous)
print(f"Check: {S_1_check:.2f}")  # Should be 110
```

---

## 3.3 PARTIAL DERIVATIVES

### Definition (Two variables)

```
∂f/∂x = lim (h→0) [f(x+h, y) - f(x, y)] / h

∂f/∂y = lim (h→0) [f(x, y+h) - f(x, y)] / h
```

**Notation**:
- ∂f/∂x: "partial derivative of f with respect to x"
- Hold all other variables constant

### Example

```
f(x, y) = x²y + 3xy²

∂f/∂x = 2xy + 3y²  (treat y as constant)
∂f/∂y = x² + 6xy   (treat x as constant)
```

### Second-Order Partials

```
∂²f/∂x² = ∂/∂x(∂f/∂x)     (second derivative w.r.t. x)
∂²f/∂x∂y = ∂/∂y(∂f/∂x)    (mixed partial)
```

**Clairaut's theorem**: If continuous, then ∂²f/∂x∂y = ∂²f/∂y∂x

---

## 3.4 GRADIENT AND HESSIAN

### Gradient (First-order)

**For f: ℝⁿ → ℝ**:

```
∇f = [∂f/∂x₁, ∂f/∂x₂, ..., ∂f/∂xₙ]ᵀ
```

**Interpretation**: Direction of steepest ascent

**Example - Portfolio optimization**:
```
f(w) = wᵀΣw  (portfolio variance)

∇f = 2Σw  (gradient w.r.t. weights)
```

### Hessian (Second-order)

```
H = [
  ∂²f/∂x₁²      ∂²f/∂x₁∂x₂  ...
  ∂²f/∂x₂∂x₁    ∂²f/∂x₂²    ...
  ...
]
```

**Properties**:
- Symmetric (Clairaut)
- Positive definite ⟹ f is convex
- Used in Newton's method for optimization

---

## 3.5 TAYLOR SERIES

### Single Variable

**Taylor expansion around x = a**:

```
f(x) = f(a) + f'(a)(x-a) + f''(a)(x-a)²/2! + f'''(a)(x-a)³/3! + ...
```

**Maclaurin series** (a = 0):
```
f(x) = f(0) + f'(0)x + f''(0)x²/2! + f'''(0)x³/3! + ...
```

### Key Series (Memorize!)

```
e^x = 1 + x + x²/2! + x³/3! + ...

ln(1+x) = x - x²/2 + x³/3 - x⁴/4 + ...  (for |x| < 1)

sin(x) = x - x³/3! + x⁵/5! - ...

cos(x) = 1 - x²/2! + x⁴/4! - ...

(1+x)^α = 1 + αx + α(α-1)x²/2! + ...
```

### First-Order Approximation

**For small h**:
```
f(x+h) ≈ f(x) + f'(x)h
```

**Finance example** - Option value change:
```
C(S + ΔS, t) ≈ C(S, t) + ∂C/∂S · ΔS
              = C(S, t) + Δ · ΔS
```

### Second-Order Approximation

```
f(x+h) ≈ f(x) + f'(x)h + ½f''(x)h²
```

**Finance example** - Include gamma:
```
C(S + ΔS) ≈ C(S) + Δ·ΔS + ½Γ·(ΔS)²
```

---

## 3.6 MULTIVARIATE TAYLOR SERIES

### Two Variables

```
f(x+h, y+k) ≈ f(x,y) + ∂f/∂x·h + ∂f/∂y·k
            + ½(∂²f/∂x²·h² + 2∂²f/∂x∂y·hk + ∂²f/∂y²·k²)
```

**Matrix form** (general):
```
f(x + h) ≈ f(x) + ∇f(x)ᵀh + ½hᵀH(x)h
```

### Itô's Lemma Preview

**For option value C(S, t)**:

```
dC ≈ ∂C/∂S·dS + ∂C/∂t·dt + ½·∂²C/∂S²·(dS)²

This is a STOCHASTIC Taylor expansion!
```

---

## 3.7 INTEGRATION

### Definite Integral

```
∫[a,b] f(x) dx = Area under curve from a to b
```

### Fundamental Theorem of Calculus

```
If F'(x) = f(x), then ∫[a,b] f(x) dx = F(b) - F(a)
```

### Key Integrals

```
∫ x^n dx = x^(n+1)/(n+1) + C  (n ≠ -1)

∫ 1/x dx = ln|x| + C

∫ e^x dx = e^x + C

∫ sin(x) dx = -cos(x) + C

∫ cos(x) dx = sin(x) + C

∫ 1/(1+x²) dx = arctan(x) + C
```

### Integration by Parts

```
∫ u dv = uv - ∫ v du
```

**Example**:
```
∫ x·e^x dx

Let u = x, dv = e^x dx
Then du = dx, v = e^x

= x·e^x - ∫ e^x dx
= x·e^x - e^x + C
= e^x(x - 1) + C
```

---

## 3.8 EXPONENTIAL AND LOGARITHM

### Properties

```
e^(a+b) = e^a · e^b
ln(ab) = ln(a) + ln(b)
ln(a^b) = b·ln(a)
e^(ln x) = x
ln(e^x) = x
```

### Derivatives

```
d/dx(e^(ax)) = a·e^(ax)
d/dx(ln(ax)) = 1/x
d/dx(a^x) = a^x · ln(a)
```

### Finance Application - Continuously Compounded Returns

**Discrete return**:
```
R_t = (S_t - S_{t-1}) / S_{t-1} = S_t/S_{t-1} - 1
```

**Log return** (continuously compounded):
```
r_t = ln(S_t / S_{t-1}) = ln(S_t) - ln(S_{t-1})
```

**Why log returns?**:
1. Symmetric: r(up 10%) = -r(down 10%)
2. Time-additive: r(t₁ to t₃) = r(t₁ to t₂) + r(t₂ to t₃)
3. Approximately Normal (for small returns)

```python
import numpy as np

prices = np.array([100, 105, 102, 108, 110])

# Simple returns
simple_returns = np.diff(prices) / prices[:-1]
print("Simple returns:", simple_returns)

# Log returns
log_returns = np.diff(np.log(prices))
print("Log returns:", log_returns)

# For small returns, they're approximately equal
print("Difference:", simple_returns - log_returns)
```

---

## 3.9 CHAIN RULE (Deep Dive)

### Single Variable

```
d/dx f(g(x)) = f'(g(x)) · g'(x)
```

**Example**:
```
f(x) = e^(x²)
Let u = x², so f = e^u

df/dx = de^u/du · du/dx
      = e^u · 2x
      = 2x·e^(x²)
```

### Multivariate Chain Rule

**If z = f(x, y) and x = x(t), y = y(t)**:

```
dz/dt = ∂f/∂x · dx/dt + ∂f/∂y · dy/dt
```

**Example - Option value over time**:
```
C(S(t), t)

dC/dt = ∂C/∂S · dS/dt + ∂C/∂t
      = Δ · dS/dt + Θ

where Θ = ∂C/∂t (theta, time decay)
```

### Total Differential

```
df = ∂f/∂x dx + ∂f/∂y dy
```

**This is KEY for Itô's lemma!**

---

## 3.10 CONVEXITY AND CONCAVITY

### Definitions

**f is convex if**:
```
f(λx + (1-λ)y) ≤ λf(x) + (1-λ)f(y)  for all λ ∈ [0,1]
```

**Equivalently** (if f''):
```
f''(x) ≥ 0  for all x
```

**f is concave if -f is convex** (or f'' ≤ 0)

### Financial Interpretation

**Convex function**: "Risk-seeking"
- Payoff increases faster than loss
- Example: Long call option

**Concave function**: "Risk-averse"  
- Utility function U(w) is typically concave
- Prefer sure $100 over 50-50 bet of $0 or $200

### Jensen's Inequality (Revisited)

**If f is convex**:
```
f(E[X]) ≤ E[f(X)]
```

**If f is concave**:
```
f(E[X]) ≥ E[f(X)]
```

### Example - Why Risk Premium Exists

```
U(w) = ln(w)  (concave utility)

E[U(W)] < U(E[W])

So you'd pay a premium to avoid risk!
```

---

## 3.11 OPTIMIZATION

### Unconstrained Optimization

**Find x* that minimizes f(x)**:

**First-order condition** (FOC):
```
f'(x*) = 0  (critical point)
```

**Second-order condition** (SOC):
```
f''(x*) > 0  ⟹ local minimum
f''(x*) < 0  ⟹ local maximum
```

### Multivariate Optimization

**FOC**:
```
∇f(x*) = 0  (all partial derivatives = 0)
```

**SOC**: Check Hessian H(x*)
- H positive definite ⟹ local minimum
- H negative definite ⟹ local maximum

### Example - Portfolio Optimization

```
Minimize f(w) = wᵀΣw
subject to wᵀ1 = 1  (fully invested)

Lagrangian: L = wᵀΣw + λ(1 - wᵀ1)

FOC: ∇L = 2Σw - λ1 = 0
     ⟹ w = (λ/2)Σ⁻¹1

Using constraint wᵀ1 = 1:
w* = Σ⁻¹1 / (1ᵀΣ⁻¹1)  (minimum variance portfolio)
```

---

## 3.12 LAGRANGE MULTIPLIERS

### Problem Setup

```
Minimize f(x, y)
subject to g(x, y) = 0
```

### Method

**Lagrangian**:
```
L(x, y, λ) = f(x, y) + λ·g(x, y)
```

**FOCs**:
```
∂L/∂x = 0
∂L/∂y = 0
∂L/∂λ = 0  (the constraint)
```

### Example

```
Minimize x² + y²
subject to x + y = 1

L = x² + y² + λ(1 - x - y)

∂L/∂x = 2x - λ = 0  ⟹ x = λ/2
∂L/∂y = 2y - λ = 0  ⟹ y = λ/2
∂L/∂λ = 1 - x - y = 0

From first two: x = y
From third: x + y = 1
⟹ x = y = 1/2

Minimum value = (1/2)² + (1/2)² = 1/2
```

---

## 3.13 IMPLICIT DIFFERENTIATION

### When to Use

**If F(x, y) = 0 defines y implicitly as a function of x**

### Rule

```
dF/dx = ∂F/∂x + ∂F/∂y · dy/dx = 0

⟹ dy/dx = -(∂F/∂x) / (∂F/∂y)
```

### Example - Yield Curve

**Bond pricing equation**:
```
P(y) = Σ C_t · e^(-y·t) = 0  (solve for yield y given price P)
```

**Duration** (sensitivity of price to yield):
```
dP/dy = -Σ t·C_t·e^(-y·t) = -D·P

where D = modified duration
```

---

## 3.14 L'HÔPITAL'S RULE

### Indeterminate Forms (0/0 or ∞/∞)

**If lim f(x)/g(x) is 0/0 or ∞/∞**:

```
lim (x→a) f(x)/g(x) = lim (x→a) f'(x)/g'(x)
```

### Example

```
lim (x→0) sin(x)/x

= lim (x→0) cos(x)/1  (L'Hôpital)
= cos(0) = 1
```

### Finance Example - Black-Scholes Limit

```
lim (T→0) C(S, K, T)  (call value as expiry approaches)

= max(S - K, 0)  (intrinsic value)
```

---

## 3.15 DIFFERENTIAL EQUATIONS (Preview)

### Ordinary Differential Equation (ODE)

**General form**:
```
dy/dt = f(t, y)
```

**Example - Exponential growth**:
```
dy/dt = ry  (growth rate proportional to current value)

Solution: y(t) = y₀·e^(rt)
```

### Separation of Variables

**If dy/dt = g(t)·h(y)**:

```
dy/h(y) = g(t)dt

∫ dy/h(y) = ∫ g(t)dt
```

### Finance Example - Short Rate Model

```
dr = α(μ - r)dt  (Vasicek model, deterministic case)

dr/(μ - r) = α dt
-ln|μ - r| = αt + C
r(t) = μ + (r₀ - μ)e^(-αt)
```

**Next chapter**: Add randomness → Stochastic Differential Equations!

---

## 3.16 SUMMARY

**What you now understand**:
✅ Derivatives and chain rule
✅ Partial derivatives and gradient
✅ Taylor series (single and multivariate)
✅ Integration and fundamental theorem
✅ Exponential and logarithm properties
✅ Convexity and Jensen's inequality
✅ Optimization (unconstrained and Lagrangian)
✅ Implicit differentiation
✅ Differential equations basics

**Next Chapter**: Stochastic Calculus—Brownian motion, Itô's lemma, SDEs

**Practice Problems**:

1. **Compute**: d/dx(e^(x²) · ln(x))
2. **Taylor expand**: e^x around x = 1 to 3rd order
3. **Optimize**: Minimize f(x, y) = x² + 2y² subject to x + y = 1
4. **Differential equation**: Solve dy/dt = -2y with y(0) = 5

