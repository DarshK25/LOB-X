#!/usr/bin/env python3
"""Diagnostic: test queue_ahead model for bid/ask fills."""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.execution.shadow_engine import ShadowQuoteEngine, ExecutionAssumptions
from lobx.market_data.trade_stream import TradeTick
from lobx.strategy.base import Quote, QuotePair

print("=== QUEUE_AHEAD MODEL DIAGNOSTIC ===\n")

# Create shadow engine with queue_ahead model
engine = ShadowQuoteEngine(
    assumptions=ExecutionAssumptions(
        maker_fee_rate=0.0,
        latency_ms=0,
        fill_ratio=1.0,
        queue_model="queue_ahead",  # Test the actual queue model
    )
)

timestamp = 1000
mid = 100.0
engine.update_market_context(timestamp=timestamp, mid=mid, best_bid=99.9, best_ask=100.1)

# Create quotes with significant queue ahead
quotes = QuotePair(
    bid=Quote(price=99.5, qty=0.01),
    ask=Quote(price=100.5, qty=0.01),
)

print(f"Creating quotes at t={timestamp}:")
print(f"  BID: {quotes.bid.qty} @ {quotes.bid.price}")
print(f"  ASK: {quotes.ask.qty} @ {quotes.ask.price}")
print(f"  Queue ahead BID: 1.0 (large queue)")
print(f"  Queue ahead ASK: 1.0 (large queue)")

engine.update_quotes(quotes, timestamp, bid_queue_ahead=1.0, ask_queue_ahead=1.0)

print(f"\nEngine state:")
print(f"  Resting bid: queue_ahead={engine.resting_bid.queue_ahead_qty}")
print(f"  Resting ask: queue_ahead={engine.resting_ask.queue_ahead_qty}")

# Consume queue on bid side
print(f"\n--- Consuming BID Queue ---")
for i in range(200):
    trade = TradeTick(
        event_time=timestamp + 10 + i,
        trade_id=100 + i,
        price=99.5,
        qty=0.01,  # Small trades
        is_buyer_maker=True,  # SELL aggressor hits bids
        symbol="BTCUSDT"
    )
    engine.on_trade(trade)
    
    if engine.resting_bid and engine.resting_bid.queue_ahead_qty <= 0:
        print(f"  Queue exhausted after {i+1} trades")
        break

buy_fills = [f for f in engine.fills if f["side"] == "buy"]
print(f"  Buy fills: {len(buy_fills)}")

# Consume queue on ask side
print(f"\n--- Consuming ASK Queue ---")
for i in range(200):
    trade = TradeTick(
        event_time=timestamp + 1010 + i,
        trade_id=1100 + i,
        price=100.5,
        qty=0.01,  # Small trades
        is_buyer_maker=False,  # BUY aggressor lifts asks
        symbol="BTCUSDT"
    )
    engine.on_trade(trade)
    
    if engine.resting_ask and engine.resting_ask.queue_ahead_qty <= 0:
        print(f"  Queue exhausted after {i+1} trades")
        break

sell_fills = [f for f in engine.fills if f["side"] == "sell"]
print(f"  Sell fills: {len(sell_fills) - len(buy_fills)}")  # New sells only

print(f"\n=== SUMMARY ===")
print(f"Total fills: {len(engine.fills)}")
print(f"  Buy fills:  {len(buy_fills)}")
print(f"  Sell fills: {len(sell_fills)}")

if len(buy_fills) > 0 and len(sell_fills) > 0:
    print("\n✓ PASS: Both bid and ask can reach queue exhaustion and fill")
else:
    print("\n✗ FAIL: One side cannot fill")
    if len(buy_fills) == 0:
        print("  Problem: BID orders never filled")
    if len(sell_fills) == 0:
        print("  Problem: ASK orders never filled")
