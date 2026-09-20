#!/usr/bin/env python3
"""Diagnostic: analyze actual backtest to find why sells don't fill."""
import sys
from pathlib import Path
import pandas as pd

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Create minimal synthetic data
print("Creating synthetic market data...")

# Simple scenario: mid oscillates 100 +/- 0.5
timestamps = list(range(0, 60000, 100))  # 60 seconds, 100ms intervals
n = len(timestamps)

# Depth events - START WITH FULL SNAPSHOT (required by replay)
depth_data = []

# Initial snapshot at t=0 with 100+ rows (required by validator)
for i in range(60):
    depth_data.append({"event_time": 0, "side": "bid", "price": 100.0 - 0.1 - i * 0.05, "qty": 10.0})
    depth_data.append({"event_time": 0, "side": "ask", "price": 100.0 + 0.1 + i * 0.05, "qty": 10.0})

# Then alternating updates
for i, ts in enumerate(timestamps[1:], 1):
    mid = 100.0 + 0.5 * (i % 2)  # Oscillate
    depth_data.append({"event_time": ts, "side": "bid", "price": mid - 0.1, "qty": 10.0})
    depth_data.append({"event_time": ts, "side": "ask", "price": mid + 0.1, "qty": 10.0})

df_depth = pd.DataFrame(depth_data)

# Trade events (both buy and sell aggressors)
trade_data = []
trade_id = 1
for i, ts in enumerate(timestamps[::10]):  # Every second
    mid = 100.0 + 0.5 * ((i *10) % 2)
    
    # SELL aggressor (hits bids)
    trade_data.append({
        "event_time": ts,
        "trade_id": trade_id,
        "price": mid - 0.05,
        "qty": 0.01,
        "is_buyer_maker": True,
        "symbol": "BTCUSDT"
    })
    trade_id += 1
    
    # BUY aggressor (lifts asks)
    trade_data.append({
        "event_time": ts + 50,
        "trade_id": trade_id,
        "price": mid + 0.05,
        "qty": 0.01,
        "is_buyer_maker": False,
        "symbol": "BTCUSDT"
    })
    trade_id += 1

df_trades = pd.DataFrame(trade_data)

print(f"Created {len(df_depth)} depth events, {len(df_trades)} trades")
print(f"  Sell aggressor trades: {(df_trades.is_buyer_maker == True).sum()}")
print(f"  Buy aggressor trades:  {(df_trades.is_buyer_maker == False).sum()}")

# Run backtest
from lobx.backtest.replay import HistoricalReplay, BacktestConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.execution.shadow_engine import ExecutionAssumptions

config = BacktestConfig(
    name="diagnostic",
    quote_interval_ms=1000,  # Quote once per second
    execution=ExecutionAssumptions(
        maker_fee_rate=0.0,
        latency_ms=0,
        fill_ratio=1.0,
        queue_model="shadow",  # Instant fills to isolate strategy issue
    ),
)

replay = HistoricalReplay(config)

def make_strategy():
    return AvellanedaStoikovStrategy(
        gamma=0.01,  # Lower risk aversion = tighter quotes
        kappa=5.0,   # Higher order flow = tighter quotes
        horizon_seconds=60.0,
        qty=0.001,
        min_spread=0.01,  # Smaller min spread
    )

print("\nRunning backtest...")
result = replay.run(df_depth, df_trades, make_strategy)

print(f"\n=== RESULTS ===")
print(f"Total fills: {len(result.fills)}")

buy_fills = [f for f in result.fills if f["side"] == "buy"]
sell_fills = [f for f in result.fills if f["side"] == "sell"]

print(f"  Buy fills:  {len(buy_fills)}")
print(f"  Sell fills: {len(sell_fills)}")

# Analyze quotes
quote_events = [e for e in result.events if e["event_type"] == "QUOTE_CALCULATED"]
print(f"\nTotal quotes generated: {len(quote_events)}")

bids_at_market = 0
asks_at_market = 0
bids_too_wide = 0
asks_too_wide = 0

for qe in quote_events:
    market_mid = qe["market_mid"]
    market_bid = qe["market_bid"]
    market_ask = qe["market_ask"]
    quote_bid = qe["bid_price"]
    quote_ask = qe["ask_price"]
    
    if quote_bid is None or quote_ask is None:
        continue
    
    # Check if quotes are competitive
    if quote_bid >= market_bid:
        bids_at_market += 1
    elif quote_bid < market_bid - 1.0:
        bids_too_wide += 1
    
    if quote_ask <= market_ask:
        asks_at_market += 1
    elif quote_ask > market_ask + 1.0:
        asks_too_wide += 1

print(f"\nQuote competitiveness:")
print(f"  Bids at/above market: {bids_at_market}")
print(f"  Bids too wide: {bids_too_wide}")
print(f"  Asks at/below market: {asks_at_market}")
print(f"  Asks too wide: {asks_too_wide}")

# Show sample quotes
print(f"\n=== SAMPLE QUOTES ===")
for qe in quote_events[:5]:
    print(f"t={qe['timestamp']}: mid={qe['market_mid']:.2f}, bid={qe['bid_price']:.2f}, ask={qe['ask_price']:.2f}, inv={qe['inventory']:.4f}")

# Analyze why fills happened
print(f"\n=== FILL ANALYSIS ===")
for fill in result.fills[:5]:
    oid = fill["order_id"]
    # Find the order creation event
    order_event = next((e for e in result.events if e.get("event_type") == "ORDER_CREATED" and e.get("order_id") == oid), None)
    if order_event:
        print(f"{fill['side'].upper()} fill: order@{order_event['price']:.2f}, filled@{fill['price']:.2f}")

if len(buy_fills) == 0 or len(sell_fills) == 0:
    print(f"\n✗ IMBALANCE DETECTED")
    if len(sell_fills) == 0:
        print(f"  Problem: No sell fills")
        # Check if ask orders were ever created
        ask_orders = [e for e in result.events if e.get("event_type") == "ORDER_CREATED" and e.get("side") == "ask"]
        print(f"  Ask orders created: {len(ask_orders)}")
        if ask_orders:
            print(f"  Sample ask: {ask_orders[0]['price']:.2f}")
            # Check if buy aggressors ever reached that price
            buy_agg_trades = [e for e in result.events if e.get("event_type") == "TRADE_RECEIVED" and not e.get("is_buyer_maker")]
            print(f"  Buy aggressor trades: {len(buy_agg_trades)}")
            if buy_agg_trades:
                print(f"  Buy aggressor price range: {min(e['price'] for e in buy_agg_trades):.2f} - {max(e['price'] for e in buy_agg_trades):.2f}")
else:
    print(f"\n✓ Both sides filled successfully")
