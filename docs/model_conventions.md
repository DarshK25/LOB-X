# AS Model Conventions

Iteration 1 establishes one convention for the canonical Avellaneda-Stoikov quote calculation.

## Units

- `mid`, `reservation_price`, and quote prices are in the asset quote currency (USD/USDT for BTCUSDT).
- `inventory` and quote quantity are in base-asset units (BTC for BTCUSDT).
- `time_remaining` is measured in seconds.
- `sigma` is measured in price units per square-root second.
- `kappa` is measured as inverse quote-currency distance when distance is expressed in price units.

The model is:

$$
r = s - q\\gamma\\sigma^2\\tau
$$

$$
\\delta = \\gamma\\sigma^2\\tau + \\frac{2}{\\gamma}\\ln\\left(1 + \\frac{\\gamma}{\\kappa}\\right)
$$

$$
bid = r - \\frac{\\delta}{2}, \\qquad ask = r + \\frac{\\delta}{2}
$$

The canonical composition is `lobx.models.avellaneda.model.calculate_quote`. The live strategy and historical replay use the same `AvellanedaStoikovStrategy`, so they share the same calculation and rounding rules.

## Audit output

`ShadowQuoteEngine.events` records `ORDER_CREATED`, `ORDER_CANCELED`, `TRADE_RECEIVED`, and `FILL` events. A historical replay writes the complete event trail to `<strategy>_<config>_events.csv`, in addition to fills and equity output.

A fill is simulated only when an eligible aggressor trade crosses an active shadow quote. The event record includes the public trade ID, quote order ID, fill quantity, remaining quantity, inventory, realized PnL, fee, and fill reason.

## Scope and limitations

This is a paper execution model. `queue_ahead` approximates public L2 queue volume; it cannot reconstruct participant-level exchange priority. Calibration and simulation results must report the selected execution assumptions, including latency, fill ratio, queue model, and fees.
