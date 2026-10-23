# LOB-X Decisions Log

## 2026-10 — R-01 sigma units and inventory skew

The calibration and rolling realized-volatility pipelines report log-return
volatility per square-root second. The Avellaneda-Stoikov pricing equations use
price volatility in the same price units as the quote distance and kappa
distance. LOB-X therefore converts `sigma_log` to `sigma_usd` exactly once at
the strategy/model boundary with `sigma_usd = sigma_log * mid`.

The earlier P2-07 inventory-skew evidence used log-unit sigma in a USD-price
formula and is invalid. It is superseded by the R-01 oracle and integration
tests, which compare reservation-price shifts in USD and ticks.

## 2026-10 — R-07 unwind scope

The current implementation is an **Almgren-Chriss-style unwind planner**. It
generates reduce-only slices for a broker adapter but does not submit exchange
orders or claim execution evidence.
