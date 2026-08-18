# Execution — Almgren-Chriss

## Model

Minimise:

$$E[C] + \lambda \text{Var}[C]$$

Subject to liquidating $X$ shares over $T$ periods.

Closed-form trajectory:

$$\tilde{x}_j = X \frac{\sinh(\kappa(T-t_j))}{\sinh(\kappa T)}$$

where $\kappa = \sqrt{\lambda \sigma^2 / \eta}$.

## Parameters

| Param | Description |
|-------|-------------|
| $X$ | Total shares to liquidate |
| $T$ | Number of periods |
| $\sigma$ | Per-period volatility |
| $\eta$ | Temporary market impact coefficient |
| $\gamma$ | Permanent market impact |
| $\lambda$ | Risk aversion |

## Implementation

`backend/python/lobx/models/almgren/`:
- `trajectory.py` — `optimal_trajectory()`, `trade_list()`
- `executor.py` — `AlmgrenChrissExecutor` — submits child orders to C++ book

## Usage

```python
from lobx.models.almgren import AlmgrenChrissExecutor
exec = AlmgrenChrissExecutor(book, tid, total_shares=10000, is_sell=True,
                              T=100, sigma=1.0, eta=0.1, gamma=0.01, lambda_=1e-6)
# Call exec.step() once per simulation tick
```
