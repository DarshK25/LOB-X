#!/usr/bin/env python3
"""Simple diagnostic: create synthetic scenario to test bid/ask fill logic."""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.execution.shadow_engine import ShadowQuoteEngine, ShadowOrder, ExecutionAssumptions
from lobx.market_data.trade_stream import TradeTick
from lobx.strategy.base import Quote, QuotePair

print("=== BID/ASK FILL DIAGNOSTIC ===\n")

# Create shadow engine
engine = ShadowQuoteEngine(
    assumptions=ExecutionAssumptions(
        maker_fee_rate=0.0,
        latency_ms=0,
        fill_ratio=1.0,
        queue_model="shadow",  # Simple model to isolate issue
    )
)

# Set market context
timestamp = 1000
mid = 100.0
engine.update_market_context(timestamp=timestamp, mid=mid, best_bid=99.9, best_ask=100.1)

# Create quotes: bid at 99.5, ask at 100.5
quotes = QuotePair(
    bid=Quote(price=99.5, qty=0.01),
    ask=Quote(price=100.5, qty=0.01),
)

print(f"Creating quotes at t={timestamp}:")
print(f"  BID: {quotes.bid.qty} @ {quotes.bid.price}")
print(f"  ASK: {quotes.ask.qty} @ {quotes.ask.price}")

engine.update_quotes(quotes, timestamp, bid_queue_ahead=0.0, ask_queue_ahead=0.0)

print(f"\nEngine state:")
print(f"  Resting bid: {engine.resting_bid}")
print(f"  Resting ask: {engine.resting_ask}")

# Scenario 1: SELL aggressor hits our BID
print(f"\n--- Scenario 1: SELL Aggressor ---")
print(f"Trade: price=99.5, qty=0.005, is_buyer_maker=True (SELL aggressor)")
print(f"Expected: Fill our BID order → we BUY")

trade1 = TradeTick(
    event_time=timestamp + 100,
    trade_id=1,
    price=99.5,
    qty=0.005,
    is_buyer_maker=True,  # SELL aggressor
    symbol="BTCUSDT"
)

fills_before = len(engine.fills)
engine.on_trade(trade1)
fills_after = len(engine.fills)

if fills_after > fills_before:
    fill = engine.fills[-1]
    print(f"✓ FILL: {fill['side'].upper()} {fill['qty']} @ {fill['price']}")
    if fill['side'] != "buy":
        print(f"  ERROR: Expected 'buy', got '{fill['side']}'")
else:
    print(f"✗ NO FILL (expected a fill)")

# Scenario 2: BUY aggressor lifts our ASK
print(f"\n--- Scenario 2: BUY Aggressor ---")
print(f"Trade: price=100.5, qty=0.005, is_buyer_maker=False (BUY aggressor)")
print(f"Expected: Fill our ASK order → we SELL")

trade2 = TradeTick(
    event_time=timestamp + 200,
    trade_id=2,
    price=100.5,
    qty=0.005,
    is_buyer_maker=False,  # BUY aggressor
    symbol="BTCUSDT"
)

fills_before = len(engine.fills)
engine.on_trade(trade2)
fills_after = len(engine.fills)

if fills_after > fills_before:
    fill = engine.fills[-1]
    print(f"✓ FILL: {fill['side'].upper()} {fill['qty']} @ {fill['price']}")
    if fill['side'] != "sell":
        print(f"  ERROR: Expected 'sell', got '{fill['side']}'")
else:
    print(f"✗ NO FILL (expected a fill)")

# Summary
print(f"\n=== SUMMARY ===")
print(f"Total fills: {len(engine.fills)}")
buy_fills = [f for f in engine.fills if f["side"] == "buy"]
sell_fills = [f for f in engine.fills if f["side"] == "sell"]
print(f"  Buy fills:  {len(buy_fills)}")
print(f"  Sell fills: {len(sell_fills)}")

if len(buy_fills) == 1 and len(sell_fills) == 1:
    print("\n✓ PASS: Both bid and ask can fill correctly")
else:
    print("\n✗ FAIL: Imbalanced fills detected")
    print("\nDEBUG: All fills:")
    for f in engine.fills:
        print(f"  {f['side'].upper()} {f['qty']} @ {f['price']} (order {f['order_id']})")
