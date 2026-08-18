# CHAPTER 4: STOCHASTIC CALCULUS - THE HEART OF QUANT FINANCE

**Goal**: Master Brownian motion, Itô's lemma, and stochastic differential equations—the foundation of derivatives pricing and optimal control.

---

## 4.1 WHY STOCHASTIC CALCULUS?

### The Problem with Deterministic Calculus

**Stock prices are RANDOM**:
```
S(t+dt) = S(t) + ???  (not deterministic!)
```

**Regular calculus**:
```
dS/dt = f(S, t)  ⟹  dS = f(S, t)dt
```

**Stochastic calculus**:
```
dS = μS dt + σS dW  (drift + randomness)
```

### Key Difference

**Deterministic**: dS² = 0 (infinitesimal squared vanishes)
**Stochastic**: (dW)² = dt (doesn't vanish!)

This changes EVERYTHING about calculus rules.

---

## 4.2 BROWNIAN MOTION (Wiener Process)

### Definition

**A continuous-time stochastic process W(t) is a Brownian motion if**:

1. **W(0) = 0** (starts at zero)
2. **Independent increments**: W(t) - W(s) independent of W(u) - W(v) for non-overlapping intervals
3. **Stationary increments**: W(t) - W(s) ~ N(0, t-s)
4. **Continuous paths**: W(t) is continuous in t

### Properties

```
E[W(t)] = 0  (zero mean)
Var(W(t)) = t  (variance grows linearly with time)
Cov(W(s), W(t)) = min(s, t)
```

**Key insight**: W(t) ~ N(0, t), so W(1) ~ N(0, 1)

### Visualization

```python
import numpy as np
import matplotlib.pyplot as plt

# Simulate Brownian motion
np.random.seed(42)
T = 1.0
N = 1000
dt = T / N
t = np.linspace(0, T, N+1)

dW = np.sqrt(dt) * np.random.randn(N)
W = np.concatenate(([0], np.cumsum(dW)))

# Plot 5 paths
plt.figure(figsize=(10, 6))
for i in range(5):
    dW = np.sqrt(dt) * np.random.randn(N)
    W = np.concatenate(([0], np.cumsum(dW)))
    plt.plot(t, W, alpha=0.7)

plt.xlabel("Time t")
plt.ylabel("W(t)")
plt.title("Brownian Motion Paths")
plt.grid(True)
plt.show()
```

**Observation**: Paths are continuous but very "jagged" (nowhere differentiable!)

---

## 4.3 PROPERTIES OF BROWNIAN MOTION

### Scaling Property

**If W(t) is Brownian motion, then**:
```
W̃(t) = c·W(t/c²)  is also Brownian motion
```

**Consequence**: Fractals! Zooming in looks the same.

### Non-Differentiability

**W(t) is nowhere differentiable**:
```
dW/dt does NOT exist in classical sense
```

**But**: We can write dW symbolically and use it in Itô calculus!

### Quadratic Variation

**Key property** (distinguishes stochastic from deterministic):

```
[W, W]_t = lim (Σ (W(t_{i+1}) - W(t_i))²) = t
```

**In differential notation**:
```
(dW)² = dt  (THIS IS CRITICAL!)
```

**Compare to regular calculus**: (dx)² = 0

### Itô Isometry

```
E[(∫₀ᵗ f(s) dW(s))²] = E[∫₀ᵗ f(s)² ds]
```

Used to compute variances of stochastic integrals.

---

## 4.4 STOCHASTIC DIFFERENTIAL EQUATIONS (SDE)

### General Form

```
dX(t) = μ(X, t)dt + σ(X, t)dW(t)
```

**Components**:
- μ(X, t): Drift (deterministic trend)
- σ(X, t): Diffusion (volatility, random component)
- dW(t): Brownian motion increment

### Geometric Brownian Motion (Stock Prices)

```
dS = μS dt + σS dW
```

**Interpretation**:
- In time dt, stock changes by:
  - Drift: μS dt (expected return)
  - Random shock: σS dW ~ N(0, σ²S²dt)

**Solution** (see next section):
```
S(t) = S₀ exp((μ - σ²/2)t + σW(t))
```

**Why the -σ²/2 term?** Itô's lemma! (Coming up)

---

## 4.5 ITÔ'S LEMMA (The Chain Rule for SDEs)

### Statement

**If X(t) follows**:
```
dX = μ dt + σ dW
```

**And f(X, t) is a smooth function, then**:

```
df = (∂f/∂t + μ·∂f/∂X + ½σ²·∂²f/∂X²) dt + σ·∂f/∂X dW
```

**Compare to regular chain rule**:
```
df = (∂f/∂t + μ·∂f/∂X) dt  (NO second derivative term!)
```

**The ½σ²·∂²f/∂X² term comes from (dW)² = dt**

### Derivation (Intuition)

**Taylor expand f(X + dX, t + dt) around (X, t)**:

```
df = ∂f/∂X·dX + ∂f/∂t·dt + ½·∂²f/∂X²·(dX)²  (keep second order!)

Substitute dX = μ dt + σ dW:

(dX)² = (μ dt + σ dW)²
      = μ²(dt)² + 2μσ dt dW + σ²(dW)²
      = σ²dt  (other terms vanish as dt → 0)

Therefore:
df = (∂f/∂t + μ·∂f/∂X + ½σ²·∂²f/∂X²) dt + σ·∂f/∂X dW
```

### Rules for Products

```
dt · dt = 0
dt · dW = 0
dW · dW = dt  ← KEY RULE!
```

---

## 4.6 EXAMPLE: GEOMETRIC BROWNIAN MOTION SOLUTION

### Problem

**Solve**:
```
dS = μS dt + σS dW
S(0) = S₀
```

### Solution via Itô's Lemma

**Let X = ln(S), so S = e^X**

**By Itô's lemma** with f(S) = ln(S):

```
∂f/∂S = 1/S
∂²f/∂S² = -1/S²

dX = d(ln S)
   = (1/S·dS - ½·1/S²·(dS)²)
   = (1/S·(μS dt + σS dW) - ½·1/S²·σ²S²dt)
   = μ dt + σ dW - ½σ² dt
   = (μ - σ²/2) dt + σ dW
```

**This is linear!**

**Integrate**:
```
X(t) = X(0) + (μ - σ²/2)t + σW(t)
ln(S(t)) = ln(S₀) + (μ - σ²/2)t + σW(t)

S(t) = S₀ exp((μ - σ²/2)t + σW(t))
```

**The -σ²/2 term is the Itô correction!**

### Simulation

```python
import numpy as np
import matplotlib.pyplot as plt

S0 = 100
mu = 0.10  # 10% drift
sigma = 0.20  # 20% vol
T = 1.0
N = 1000
dt = T / N
t = np.linspace(0, T, N+1)

# Simulate 100 paths
plt.figure(figsize=(10, 6))
for _ in range(100):
    dW = np.sqrt(dt) * np.random.randn(N)
    W = np.concatenate(([0], np.cumsum(dW)))
    S = S0 * np.exp((mu - 0.5*sigma**2)*t + sigma*W)
    plt.plot(t, S, alpha=0.3, linewidth=0.5)

plt.xlabel("Time")
plt.ylabel("Stock Price")
plt.title(f"Geometric Brownian Motion (μ={mu}, σ={sigma})")
plt.grid(True)
plt.show()

# Expected value at T=1
print(f"E[S(1)] = {S0 * np.exp(mu)} (theoretical)")
```

---

## 4.7 BLACK-SCHOLES PDE DERIVATION

### Setup

**Stock price**:
```
dS = μS dt + σS dW
```

**Call option**: C(S, t) with payoff C(S, T) = max(S - K, 0)

### Goal

**Find PDE that C must satisfy**

### Method: Delta Hedging

**Portfolio**: Π = V - ΔS (short option, long Δ shares)

**Change in portfolio** (using Itô):

```
dΠ = dV - Δ dS

dV = ∂V/∂t dt + ∂V/∂S dS + ½σ²S²·∂²V/∂S² dt  (Itô's lemma)

dΠ = (∂V/∂t + ½σ²S²·∂²V/∂S²) dt + (∂V/∂S - Δ) dS
```

**Choose Δ = ∂V/∂S** (delta hedge):

```
dΠ = (∂V/∂t + ½σ²S²·∂²V/∂S²) dt  (no randomness!)
```

**No-arbitrage**: Risk-free portfolio earns risk-free rate

```
dΠ = rΠ dt = r(V - S·∂V/∂S) dt
```

**Equate**:

```
∂V/∂t + ½σ²S²·∂²V/∂S² = r(V - S·∂V/∂S)
```

**Rearrange**:

```
∂V/∂t + rS·∂V/∂S + ½σ²S²·∂²V/∂S² - rV = 0
```

**This is the Black-Scholes PDE!**

### Terminal Condition

```
C(S, T) = max(S - K, 0)
```

### Solution (for European call)

```
C(S, t) = S·N(d₁) - K·e^(-r(T-t))·N(d₂)

d₁ = (ln(S/K) + (r + σ²/2)(T-t)) / (σ√(T-t))
d₂ = d₁ - σ√(T-t)
```

### The Greeks - Why They Matter

**Greeks measure how option value changes with various factors**

#### Delta (Δ = ∂C/∂S)

**Definition**: Rate of change of option value w.r.t. stock price

**For call**: 0 < Δ < 1
**For put**: -1 < Δ < 0

**Intuition**: 
- Δ = 0.5 means: If stock moves $1, option moves $0.50
- Deep in-the-money call: Δ → 1 (moves 1-for-1 with stock)
- Far out-of-the-money call: Δ → 0 (barely moves)

**Hedging**: To be **delta-neutral**, hold -Δ shares for each option
```
Portfolio = 1 option - Δ shares
dΠ/dS ≈ 0 (no first-order exposure to stock moves)
```

**Example**:
```python
# You sold 100 call options with Δ = 0.6
# To hedge, buy: 100 × 0.6 = 60 shares
# If stock moves $1: Option loses $60, shares gain $60 → neutral
```

#### Gamma (Γ = ∂²C/∂S²)

**Definition**: Rate of change of delta w.r.t. stock price (convexity)

**Why it matters**:
- Γ > 0: Delta increases as stock goes up (good for long options)
- High Γ: Delta changes rapidly (need frequent rehedging)
- **Gamma risk**: Your hedge becomes outdated quickly

**Peak at-the-money**: Maximum gamma when S ≈ K

**Trading insight**:
```
Market makers are SHORT gamma (sold options)
→ As market moves, their hedge becomes wrong
→ Need to rehedge frequently (costs transaction fees)
→ This is why volatility is expensive!
```

#### Vega (ν = ∂C/∂σ)

**Definition**: Sensitivity to volatility

**Key insight**: Options are LONG vega
```
Higher volatility → higher option value
(More uncertainty = more chance of big payoff)
```

**Example**:
```python
# Call option: S=100, K=100, T=1, r=0.05
# σ = 20%: C ≈ $10.45
# σ = 25%: C ≈ $12.60 (higher vol → higher value)
```

**Vega risk**: You sold options, vol spikes → you lose money

#### Theta (Θ = ∂C/∂t)

**Definition**: Time decay (usually negative for long options)

**Intuition**: As expiry approaches, time value decays
```
Option value = Intrinsic value + Time value
As T → 0: Time value → 0
```

**Theta decay accelerates near expiry** (especially at-the-money)

**Trading insight**: Option sellers EARN theta (time decay works for them)

#### Why Greeks Matter for Market Making

**Avellaneda-Stoikov model** implicitly manages:
- **Delta**: Inventory management (long inventory = long delta exposure)
- **Gamma**: Reservation price shifts to reduce convexity risk
- **Vega**: Spread widens with volatility

**Your MM strategy needs**:
1. Delta hedge: Adjust quotes to reduce inventory
2. Gamma awareness: Don't accumulate too much at one price
3. Vega management: Wider spreads in high-vol regimes

---

## 4.8 RISK-NEUTRAL PRICING

### Key Insight

**In Black-Scholes PDE, μ (drift) disappears!**

**Why?** Delta hedging removes market risk.

### Risk-Neutral Measure

**Under risk-neutral probability ℚ**:
```
dS = rS dt + σS dW̃  (drift = risk-free rate)
```

**Option price**:
```
V(S, t) = e^(-r(T-t)) Eℚ[Payoff | F_t]
```

**Example - Call**:
```
C = e^(-rT) Eℚ[max(S_T - K, 0)]
```

**Interpretation**: Discount expected payoff at risk-free rate (under ℚ, not real-world ℙ!)

---

## 4.9 ITÔ INTEGRAL

### Definition

**For adapted process f(t)**:

```
I(t) = ∫₀ᵗ f(s) dW(s)
```

**Construction** (Riemann-like):

```
I(t) = lim Σ f(t_i) (W(t_{i+1}) - W(t_i))
```

### Properties

1. **E[I(t)] = 0** (martingale)
2. **Var(I(t)) = E[∫₀ᵗ f(s)² ds]** (Itô isometry)
3. **I(t) is continuous**

### Example

```
∫₀ᵗ W(s) dW(s) = ½(W(t)² - t)
```

**Proof** (using Itô):

Let f(W) = W²/2

```
df = ∂f/∂W dW + ½·∂²f/∂W² (dW)²
   = W dW + ½·1·dt
   = W dW + ½ dt

Integrate 0 to t:
W(t)²/2 - 0 = ∫₀ᵗ W(s) dW(s) + ½t

∫₀ᵗ W(s) dW(s) = W(t)²/2 - t/2
```

---

## 4.10 GIRSANOV THEOREM

### Statement

**Change of measure from ℙ to ℚ**:

```
If dW is Brownian motion under ℙ,
then W̃(t) = W(t) + ∫₀ᵗ θ(s) ds is Brownian motion under ℚ

where dℚ/dℙ = exp(-∫₀ᵀ θ(s) dW(s) - ½∫₀ᵀ θ(s)² ds)
```

### Application: Risk-Neutral Pricing

**Real-world (ℙ)**:
```
dS = μS dt + σS dW
```

**Risk-neutral (ℚ)**: Choose θ = (μ - r) / σ

```
dS = rS dt + σS dW̃  where dW̃ = dW + ((μ-r)/σ) dt
```

**Now discount at r and take expectation under ℚ!**

---

## 4.11 STOCHASTIC OPTIMAL CONTROL

### What is Optimal Control?

**Problem**: You control a system over time to maximize some objective

**Examples**:
- Spacecraft: Control thrusters to minimize fuel + maximize speed
- Trading: Control order placement to maximize profit - risk
- Inventory: Control production to minimize costs + meet demand

**Key difference from regular optimization**: State evolves STOCHASTICALLY

### Dynamic Programming Intuition

**Bellman's Principle of Optimality**:
```
"An optimal policy has the property that whatever the initial state 
and decision, the remaining decisions must be optimal with respect 
to the state resulting from the first decision."
```

**Translation**: Optimal strategy at time t depends ONLY on current state, not history

**Example - Shortest Path**:
```
To get from A → C optimally:
1. Choose best next step A → B
2. From B, choose best path B → C

Optimal A→C = Optimal A→B + Optimal B→C
```

### Value Function

**Definition**:
```
V(x, t) = max_{u(s), s∈[t,T]} E[Payoff | X_t = x]
```

**Interpretation**: Best you can do starting from state x at time t

**Boundary condition**: V(x, T) = terminal payoff

**Key insight**: V satisfies a PDE (the HJB equation)!

### Hamilton-Jacobi-Bellman (HJB) Equation

**Problem**: Maximize J = E[∫₀ᵀ U(X, u, t) dt + Φ(X_T)]

**Subject to**: dX = f(X, u, t) dt + g(X, u, t) dW

**Value function**: V(X, t) = max E[future payoff | X_t = X]

**HJB PDE**:

```
∂V/∂t + max_u {f·∂V/∂X + U(X, u, t) + ½g²·∂²V/∂X²} = 0
```

**Terminal condition**: V(X, T) = Φ(X)

### Derivation (Intuitive)

**Step 1**: Bellman equation (discrete time)
```
V(x, t) = max_u E[reward(x, u) + V(x', t+dt)]
```

**Step 2**: Expand V(x', t+dt) using Itô's lemma
```
dV = ∂V/∂t dt + ∂V/∂X dX + ½∂²V/∂X² (dX)²
```

**Step 3**: Substitute dX = f dt + g dW
```
E[dV] = (∂V/∂t + f·∂V/∂X + ½g²·∂²V/∂X²) dt
```

**Step 4**: No-arbitrage condition
```
dV + reward·dt = 0  (zero expected change in total value)

∂V/∂t + max_u {f·∂V/∂X + reward + ½g²·∂²V/∂X²} = 0
```

### Example - Consumption-Savings Problem

**Problem**: Maximize E[∫₀ᵀ U(c) dt] where c = consumption rate

**Wealth dynamics**: dW = (rW - c) dt + σW dW

**HJB equation**:
```
∂V/∂t + max_c {U(c) + (rW - c)·∂V/∂W + ½σ²W²·∂²V/∂W²} = 0
```

**FOC w.r.t. c**:
```
U'(c*) = ∂V/∂W  (marginal utility = shadow price)
```

### Avellaneda-Stoikov Application

**State**: (s, q, t) = (mid-price, inventory, time)

**Control**: (δ^bid, δ^ask) = bid/ask spreads

**Objective**: Maximize E[U(terminal wealth)]

**Dynamics**:
```
ds = σ dW  (mid-price Brownian motion)
dq = dN^bid - dN^ask  (inventory changes)
```

**HJB equation** (simplified form):
```
∂V/∂t + ½σ²·∂²V/∂s² 
+ max_{δ^bid} {λ^bid(δ^bid)·[V(s, q+1, t) - V]}
+ max_{δ^ask} {λ^ask(δ^ask)·[V(s, q-1, t) - V]}
= 0
```

**Solution steps**:
1. Guess V has CARA form: V = -exp(-γ(x + qs + θ(q,t)))
2. Substitute into HJB
3. Solve for θ(q, t)
4. Extract optimal controls δ^bid*, δ^ask*

**Result**:
- Reservation price: r = s - q·γ·σ²·(T-t)
- Optimal spread: δ = γ·σ²·(T-t) + (2/γ)·ln(1 + γ/κ)

**This is where the AS formulas come from!**

### Why HJB is Powerful

**Advantages**:
1. Turns infinite-dimensional problem (find entire strategy) into finite-dimensional (solve a PDE)
2. Gives CLOSED-FORM solutions for many problems
3. Naturally handles constraints and state-dependent control

**Limitation**: Hard to solve numerically in high dimensions (curse of dimensionality)

---

## 4.12 FEYNMAN-KAC THEOREM

### Statement

**PDE**:
```
∂V/∂t + μ(X, t)·∂V/∂X + ½σ²(X, t)·∂²V/∂X² = 0
V(X, T) = Φ(X)
```

**Has solution**:
```
V(X, t) = E[Φ(X_T) | X_t = X]

where dX = μ dt + σ dW
```

**Interpretation**: Solving a PDE = computing an expectation!

### Applications

1. **Option pricing**: Black-Scholes PDE → Expected payoff
2. **Heat equation**: Diffusion PDE → Brownian motion expectation
3. **Monte Carlo**: Simulate paths instead of solving PDE

---

## 4.13 MULTIVARIATE ITÔ'S LEMMA

### Two-Dimensional Case

**If**:
```
dX = μ_X dt + σ_X dW_X
dY = μ_Y dt + σ_Y dW_Y

where dW_X · dW_Y = ρ dt  (correlation)
```

**Then for f(X, Y, t)**:

```
df = ∂f/∂t dt + ∂f/∂X dX + ∂f/∂Y dY
   + ½(∂²f/∂X² (dX)² + 2·∂²f/∂X∂Y dX dY + ∂²f/∂Y² (dY)²)

= (∂f/∂t + μ_X·∂f/∂X + μ_Y·∂f/∂Y
   + ½σ_X²·∂²f/∂X² + ρσ_Xσ_Y·∂²f/∂X∂Y + ½σ_Y²·∂²f/∂Y²) dt
  + σ_X·∂f/∂X dW_X + σ_Y·∂f/∂Y dW_Y
```

**Key**: Cross-term ρσ_Xσ_Y·∂²f/∂X∂Y appears!

---

## 4.14 ORNSTEIN-UHLENBECK PROCESS

### Definition

```
dX = α(μ - X) dt + σ dW
```

**Interpretation**: Mean-reverting process
- If X > μ, drift pulls DOWN
- If X < μ, drift pulls UP
- Speed of mean reversion: α

### Solution

```
X(t) = μ + (X₀ - μ)e^(-αt) + σ∫₀ᵗ e^(-α(t-s)) dW(s)
```

**Stationary distribution** (as t → ∞):
```
X ~ N(μ, σ²/(2α))
```

### Application: Interest Rate Models

**Vasicek model**:
```
dr = α(μ - r) dt + σ dW
```

**Used for short-term interest rates** (mean revert to long-run mean μ)

### Simulation

```python
import numpy as np
import matplotlib.pyplot as plt

alpha = 2.0  # Mean reversion speed
mu = 0.05    # Long-run mean
sigma = 0.02 # Volatility
X0 = 0.10    # Starting value

T = 5.0
N = 1000
dt = T / N
t = np.linspace(0, T, N+1)

plt.figure(figsize=(10, 6))
for _ in range(20):
    X = np.zeros(N+1)
    X[0] = X0
    for i in range(N):
        dW = np.sqrt(dt) * np.random.randn()
        X[i+1] = X[i] + alpha*(mu - X[i])*dt + sigma*dW
    plt.plot(t, X, alpha=0.5)

plt.axhline(mu, color='r', linestyle='--', label=f'Mean = {mu}')
plt.xlabel("Time")
plt.ylabel("X(t)")
plt.title("Ornstein-Uhlenbeck Process (Mean-Reverting)")
plt.legend()
plt.grid(True)
plt.show()
```

---

## 4.15 SUMMARY

**What you now understand**:
✅ Brownian motion properties and simulation
✅ (dW)² = dt (KEY RULE!)
✅ Stochastic differential equations (SDE)
✅ Itô's lemma (chain rule for SDEs)
✅ Geometric Brownian motion solution
✅ Black-Scholes PDE derivation
✅ Risk-neutral pricing
✅ Itô integral
✅ HJB equation (optimal control)
✅ Feynman-Kac theorem
✅ Multivariate Itô's lemma
✅ Ornstein-Uhlenbeck (mean reversion)

**Next Chapter**: Application to Avellaneda-Stoikov derivation from HJB

**Practice Problems**:

1. **Simulate**: 1000 paths of GBM with S₀=100, μ=0.05, σ=0.2, T=1
2. **Itô's lemma**: If dS = μS dt + σS dW, find d(S²)
3. **Solve**: dX = -2X dt + dW with X(0) = 1 (Ornstein-Uhlenbeck with α=2, μ=0, σ=1)
4. **Price**: European call with S=100, K=100, r=0.05, σ=0.2, T=1

