# Execution — Almgren-Chriss-style unwind planner

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

## Implementation status

`backend/python/lobx/execution/unwind.py` contains the current
`AlmgrenChrissUnwindController`. It generates timestamped, reduce-only broker
plans. It is a planner, not an exchange executor: it does not submit child
orders and it is not integrated with a live execution adapter.

## Usage

```python
from lobx.execution.unwind import AlmgrenChrissUnwindController, UnwindConfig
planner = AlmgrenChrissUnwindController(UnwindConfig(horizon_ms=30_000))
plan = planner.generate_plan(position=0.005, timestamp_ms=0, reason="position_breach")
# plan.to_broker_orders() returns reduce-only payloads; no order is submitted.
```
