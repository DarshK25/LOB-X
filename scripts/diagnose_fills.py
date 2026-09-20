#!/usr/bin/env python3
"""Diagnostic script to understand why we get 154 buys / 0 sells."""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.backtest.replay import HistoricalReplay, BacktestConfig
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.execution.shadow_engine import ExecutionAssumptions

# Use Binance aggTrades (generates synthetic depth)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
TRADES_FILE = DATA_DIR / "parquet" / "BTCUSDT-futures-um-aggTrades-2024-08-01_to_2024-08-14.parquet"

if not TRADES_FILE.exists():
    print(f"ERROR: Trade file not found: {TRADES_FILE}")
    sys.exit(1)

print(f"Using trade file: {TRADES_FILE}")

# Trace execution
config = BacktestConfig(
    name="diagnostic",
    quote_interval_ms=100,
    execution=ExecutionAssumptions(
        maker_fee_rate=0.0,
        latency_ms=10,
        fill_ratio=1.0,
        queue_model="queue_ahead",  # Current model
    ),
)

replay = HistoricalReplay.from_binance_parquet(TRADES_FILE, config, spread_ticks=0.20)

def make_strategy():
    return AvellanedaStoikovStrategy(
        gamma=0.1,
        kappa=1.5,
        horizon_seconds=300.0,
        qty=0.001,
        min_spread=0.02,
    )

result = replay.run(make_strategy)

print("\n=== FILL ANALYSIS ===")
print(f"Total fills: {len(result.fills)}")

buy_fills = [f for f in result.fills if f["side"] == "buy"]
sell_fills = [f for f in result.fills if f["side"] == "sell"]

print(f"Buy fills:  {len(buy_fills)}")
print(f"Sell fills: {len(sell_fills)}")

print("\n=== SAMPLE BUY FILLS ===")
for fill in buy_fills[:3]:
    print(f"  Order {fill['order_id']}: {fill['side']} {fill['qty']} @ {fill['price']}")

print("\n=== SAMPLE SELL FILLS (if any) ===")
for fill in sell_fills[:3]:
    print(f"  Order {fill['order_id']}: {fill['side']} {fill['qty']} @ {fill['price']}")

# Analyze events
print("\n=== ORDER ANALYSIS ===")
order_created = [e for e in result.events if e["event_type"] == "ORDER_CREATED"]
bid_orders = [e for e in order_created if e["side"] == "bid"]
ask_orders = [e for e in order_created if e["side"] == "ask"]

print(f"Total orders created: {len(order_created)}")
print(f"  Bid orders: {len(bid_orders)}")
print(f"  Ask orders: {len(ask_orders)}")

# Check trades
trade_events = [e for e in result.events if e["event_type"] == "TRADE_RECEIVED"]
print(f"\nTotal trades received: {len(trade_events)}")

sell_aggressor_trades = [e for e in trade_events if e["is_buyer_maker"]]
buy_aggressor_trades = [e for e in trade_events if not e["is_buyer_maker"]]

print(f"  Sell aggressor (is_buyer_maker=True):  {len(sell_aggressor_trades)}")
print(f"  Buy aggressor (is_buyer_maker=False):  {len(buy_aggressor_trades)}")

# Check fills by trade direction
fills_from_sell_aggressor = []
fills_from_buy_aggressor = []

for fill in result.fills:
    trade_id = fill["trade_id"]
    # Find the trade event
    for e in trade_events:
        if e["trade_id"] == trade_id:
            if e["is_buyer_maker"]:
                fills_from_sell_aggressor.append(fill)
            else:
                fills_from_buy_aggressor.append(fill)
            break

print(f"\n=== FILLS BY AGGRESSOR ===")
print(f"Fills from sell aggressor (should fill BIDs): {len(fills_from_sell_aggressor)}")
print(f"Fills from buy aggressor (should fill ASKs):  {len(fills_from_buy_aggressor)}")

# Deep dive: check first few ask orders
print("\n=== FIRST 3 ASK ORDERS ===")
for i, ask_event in enumerate(ask_orders[:3]):
    order_id = ask_event["order_id"]
    print(f"\nAsk Order #{order_id}:")
    print(f"  Price: {ask_event['price']}")
    print(f"  Qty: {ask_event['qty']}")
    print(f"  Queue ahead: {ask_event['queue_ahead_qty']}")
    print(f"  Active at: {ask_event['active_at']}")
    
    # Find if this order got filled
    order_fills = [f for f in result.fills if f["order_id"] == order_id]
    if order_fills:
        print(f"  FILLED: {len(order_fills)} time(s)")
        for f in order_fills:
            print(f"    {f['side']} {f['qty']} @ {f['price']}")
    else:
        print(f"  NOT FILLED")
    
    # Find trades that could have filled it
    timestamp = ask_event["timestamp"]
    price = ask_event["price"]
    active_at = ask_event["active_at"]
    
    candidate_trades = [
        e for e in trade_events
        if e["timestamp"] >= active_at
        and not e["is_buyer_maker"]  # buy aggressor
        and e["price"] >= price  # at or above ask
    ]
    
    print(f"  Candidate buy-aggressor trades after active_at: {len(candidate_trades)}")
    if candidate_trades and not order_fills:
        print(f"  WARNING: Order should have been crossed but wasn't!")
        for ct in candidate_trades[:2]:
            print(f"    Trade {ct['trade_id']}: price={ct['price']}, qty={ct['qty']}")

print("\n=== DIAGNOSIS COMPLETE ===")
