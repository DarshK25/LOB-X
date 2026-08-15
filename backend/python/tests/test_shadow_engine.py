"""tests/test_shadow_engine.py — unit tests for ShadowQuoteEngine."""
import pytest
from lobx.strategy.base import QuotePair, Quote
from lobx.execution.shadow_engine import ShadowQuoteEngine
from lobx.execution.shadow_engine import ExecutionAssumptions
from lobx.market_data.trade_stream import TradeTick


def make_trade(price: float, qty: float, is_buyer_maker: bool, trade_id: int = 1) -> TradeTick:
    """Helper: build a TradeTick with the given fill parameters."""
    return TradeTick(
        event_time=1_000,
        trade_id=trade_id,
        price=price,
        qty=qty,
        is_buyer_maker=is_buyer_maker,
        symbol="BTCUSDT",
    )


def make_quotes(bid_price: float, ask_price: float, qty: float = 0.01) -> QuotePair:
    return QuotePair(
        bid=Quote(price=bid_price, qty=qty),
        ask=Quote(price=ask_price, qty=qty),
    )


# ── Quote placement ───────────────────────────────────────────────────────────

class TestUpdateQuotes:

    def test_places_bid_and_ask(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        assert engine.resting_bid is not None
        assert engine.resting_ask is not None
        assert engine.resting_bid.price == 99.0
        assert engine.resting_ask.price == 101.0

    def test_cancel_replace_on_price_change(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        old_bid_id = engine.resting_bid.order_id
        engine.update_quotes(make_quotes(98.0, 102.0), timestamp=1)
        # New order placed → new order_id
        assert engine.resting_bid.order_id != old_bid_id
        assert engine.resting_bid.price == 98.0

    def test_no_cancel_when_price_unchanged(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        old_bid_id = engine.resting_bid.order_id
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=1)
        # Same price → no replace → same order_id
        assert engine.resting_bid.order_id == old_bid_id

    def test_none_bid_cancels_bid(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.update_quotes(QuotePair(bid=None, ask=Quote(101.0, 0.01)), timestamp=1)
        assert engine.resting_bid is None
        assert engine.resting_ask is not None


# ── Fill logic ────────────────────────────────────────────────────────────────

class TestFills:

    def test_bid_fills_on_crossing_sell_aggressor(self):
        """is_buyer_maker=True → sell aggressor → should fill our bid."""
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True))
        assert engine.fill_count == 1
        assert engine.fills[0]["side"] == "buy"
        assert abs(engine.inventory.position - 0.01) < 1e-9

    def test_ask_fills_on_crossing_buy_aggressor(self):
        """is_buyer_maker=False → buy aggressor → should fill our ask."""
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.on_trade(make_trade(price=101.0, qty=0.01, is_buyer_maker=False))
        assert engine.fill_count == 1
        assert engine.fills[0]["side"] == "sell"
        assert abs(engine.inventory.position - (-0.01)) < 1e-9

    def test_trade_below_bid_does_not_fill_bid(self):
        """Sell aggressor at price ABOVE our bid → doesn't cross."""
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.on_trade(make_trade(price=100.0, qty=0.01, is_buyer_maker=True))
        assert engine.fill_count == 0

    def test_buy_aggressor_does_not_fill_bid(self):
        """Buy aggressor can only fill asks, not bids."""
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=False))
        assert engine.fill_count == 0

    def test_partial_fill_reduces_remaining_qty(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0, qty=0.02), timestamp=0)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True))
        assert engine.fill_count == 1
        assert abs(engine.resting_bid.remaining_qty - 0.01) < 1e-9

    def test_full_fill_clears_resting_order(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(99.0, 101.0, qty=0.01), timestamp=0)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True))
        assert engine.resting_bid is None

    def test_fill_updates_inventory_position(self):
        engine = ShadowQuoteEngine()
        engine.update_quotes(make_quotes(100.0, 102.0, qty=0.001), timestamp=0)
        engine.on_trade(make_trade(price=100.0, qty=0.001, is_buyer_maker=True))
        engine.on_trade(make_trade(price=102.0, qty=0.001, is_buyer_maker=False))
        # Wait — after bid fills, resting_bid = None (fully filled). Re-quote ask.
        # But we already had an ask at 102 resting, sell aggressor at 102 fills it.
        # Position: +0.001 (buy) -0.001 (sell) = 0
        assert abs(engine.inventory.position) < 1e-9
        assert abs(engine.inventory.realized_pnl - 0.002) < 1e-9  # (102-100)*0.001

    def test_no_fill_when_no_quote_resting(self):
        engine = ShadowQuoteEngine()   # no quotes placed
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True))
        assert engine.fill_count == 0

    def test_latency_blocks_trade_before_quote_is_active(self):
        engine = ShadowQuoteEngine(assumptions=ExecutionAssumptions(latency_ms=100))
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=1_000)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True, trade_id=1))
        assert engine.fill_count == 0

    def test_maker_fee_is_explicitly_recorded(self):
        engine = ShadowQuoteEngine(assumptions=ExecutionAssumptions(maker_fee_rate=0.001))
        engine.update_quotes(make_quotes(100.0, 102.0, qty=0.01), timestamp=0)
        engine.on_trade(make_trade(price=100.0, qty=0.01, is_buyer_maker=True))
        assert engine.fees_paid == pytest.approx(0.001)
        assert engine.fills[0]["fee"] == pytest.approx(0.001)
