# Quantitative Feature Reference

This document outlines the core quantitative features extracted by the Market Analytics Platform and their respective academic references.

## 1. Mid-Price & Microprice
- **Mid-Price**: The simple average of the best bid and best ask. $(P_{bid} + P_{ask}) / 2$.
- **Microprice**: Volume-weighted mid-price. $(P_{bid} \cdot Q_{ask} + P_{ask} \cdot Q_{bid}) / (Q_{bid} + Q_{ask})$.
- **Reference**: Cont (2011) *Statistical modeling of high-frequency financial data*.

## 2. Log-Returns
- **Definition**: $r_t = \log(P_t / P_{t-1})$
- **Usage**: Time-additive, approximately normally distributed for short intervals, prevents negative prices in continuous-time models.

## 3. Order Book Imbalance (OBI)
- **Definition**: $(Q_{bid} - Q_{ask}) / (Q_{bid} + Q_{ask})$
- **Reference**: Cartea, Jaimungal, and Penalva (2015) *Algorithmic and High-Frequency Trading*, Section 3.1.

## 4. Spread and Market Depth
- **Spread**: $P_{ask} - P_{bid}$
- **Depth**: Cumulative quantity at the Top N levels of the limit order book.

## 5. Optimal Pricing
- **Reservation Price (Inventory-shifted Mid)**: $r = s - q \cdot \gamma \cdot \sigma^2 \cdot T$
- **Spread/Width**: $w = \gamma \cdot \sigma^2 \cdot T + \frac{2}{\gamma} \ln(1 + \frac{\gamma}{\kappa})$
- **Reference**: Avellaneda & Stoikov (2008) *High-frequency trading in a limit order book*.
