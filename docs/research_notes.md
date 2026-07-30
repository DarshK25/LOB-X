# Research Notes

## Key Papers

### Avellaneda & Stoikov (2008)
- "High-frequency trading in a limit order book"
- Main insight: inventory risk drives reservation price away from mid
- Implementation: `lobx/models/avellaneda/`

### Almgren & Chriss (2000)
- "Optimal execution of portfolio transactions"
- Closed-form optimal liquidation trajectory balancing risk vs cost
- Implementation: `lobx/models/almgren/`

### Hawkes Process (Hawkes 1971)
- Self-exciting point process — trades beget more trades
- Use: calibrate arrival rate to real data, use as order generator
- Implementation: `lobx/models/hawkes/`

### Bouchaud et al. (2002)
- "Statistical properties of stock order books"
- LOB shape, queue dynamics, impact decay
- Reference for realistic synthetic order generation

## Open Questions

- [ ] Which volatility estimator to use? EWMA vs GARCH(1,1)
- [ ] How to handle partial fills in AS inventory tracking?
- [ ] What real dataset to use? LOBSTER (NASDAQ) vs Binance?
- [ ] ZeroMQ layer for Week 9–12 distributed story?

## Calibration Notes

- Hawkes μ ≈ 1–5 events/s for liquid stocks
- Typical branching ratio α/β ≈ 0.6–0.8
- AS γ ≈ 0.01–0.1 depending on risk tolerance
