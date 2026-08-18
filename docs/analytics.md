# Analytics Architecture

The Market Analytics Platform serves as the bridge between raw data collection and strategic execution. It digests order book snapshots and trade tapes into higher-order features.

## Core Modules

### 1. Spread Analyzer (`spread.py`)
Tracks the bid-ask spread to understand market width.
- **Metrics**: Average spread, median spread, distribution percentiles, histogram.
- **Usage**: Identifying periods of high liquidity stress or width.

### 2. Liquidity & Depth (`liquidity.py`)
Analyzes market depth at various levels.
- **Metrics**: Top 5 depth, Top 10 depth, Volume-Weighted Average Price (VWAP).
- **Usage**: Sizing orders and understanding market impact.

### 3. Order Flow Imbalance (`imbalance.py`)
Computes the standardized Order Book Imbalance (OBI).
- **Metric**: $(BidVol - AskVol) / (BidVol + AskVol)$
- **Usage**: Short-term directional prediction.

### 4. Returns & Volatility (`returns.py`)
Calculates the fundamental movement of price.
- **Metrics**: Log-returns, simple returns, rolling standard deviations.
- **Usage**: Input for the volatility (sigma) calibration in pricing models.
