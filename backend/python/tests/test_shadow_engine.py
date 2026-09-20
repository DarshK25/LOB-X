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

    def test_order_ids_are_engine_local_and_replacements_cancel_old_order(self):
        first = ShadowQuoteEngine()
        second = ShadowQuoteEngine()
        first.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        second.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        assert first.resting_bid.order_id == second.resting_bid.order_id == 1
        old_order = first.resting_bid
        first.update_quotes(make_quotes(98.0, 102.0), timestamp=10)
        assert old_order.status == "CANCELED"
        assert old_order.canceled_at == 10

    def test_stale_order_expires_before_trade_matching(self):
        engine = ShadowQuoteEngine(
            assumptions=ExecutionAssumptions(max_quote_age_ms=10)
        )
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True))
        assert engine.fill_count == 0
        assert any(event["event_type"] == "ORDER_EXPIRED" for event in engine.events)

    def test_reproduce_1632_stale_order_cascade_is_prevented(self):
        """Regression test for the 16:32 run bug: replaced/cancelled orders must NEVER fill on subsequent trades."""
        engine = ShadowQuoteEngine()
        # 1. Strategy initially quotes Ask @ 76501.84
        engine.update_quotes(make_quotes(76500.00, 76501.84, qty=0.001), timestamp=1_000)
        old_ask = engine.resting_ask
        assert old_ask.price == 76501.84
        assert old_ask.status in ("ACTIVE", "WAITING")

        # 2. Market shifts up to 76606.01 -> strategy updates Ask to 76606.65
        engine.update_quotes(make_quotes(76605.00, 76606.65, qty=0.001), timestamp=2_000)
        assert old_ask.status == "CANCELED"
        assert old_ask.canceled_at == 2_000
        assert engine.resting_ask.price == 76606.65

        # 3. Public trade arrives at the OLD price level (76501.84)
        trade_at_old_price = TradeTick(
            event_time=3_000, trade_id=101, price=76501.84, qty=0.001, is_buyer_maker=False, symbol="BTCUSDT"
        )
        engine.on_trade(trade_at_old_price)

        # Replaced/cancelled order MUST NOT fill
        assert engine.fill_count == 0
        assert old_ask.remaining_qty == old_ask.qty

        # 4. Public trade arrives at the CURRENT active Ask price (76606.65)
        trade_at_new_price = TradeTick(
            event_time=4_000, trade_id=102, price=76606.65, qty=0.001, is_buyer_maker=False, symbol="BTCUSDT"
        )
        engine.on_trade(trade_at_new_price)

        # Only the current active order fills
        assert engine.fill_count == 1
        assert engine.fills[0]["price"] == 76606.65
        assert engine.fills[0]["order_id"] == engine.resting_ask.order_id if engine.resting_ask is not None else 4

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
        event_types = [event["event_type"] for event in engine.events]
        assert event_types[:2] == ["ORDER_CREATED", "ORDER_WAITING"]
        assert event_types[2:4] == ["ORDER_CREATED", "ORDER_WAITING"]
        assert "TRADE_RECEIVED" in event_types
        assert "FILL" in event_types
        fill_event = engine.events[-1]
        assert fill_event["trade_id"] == 1
        assert fill_event["reason"] == "aggressor_crossed_quote"

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

    def test_fill_contains_order_age_and_market_context(self):
        engine = ShadowQuoteEngine()
        engine.update_market_context(
            timestamp=900, mid=100.0, best_bid=99.0, best_ask=101.0
        )
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=0)
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True))
        fill = engine.fills[0]
        assert fill["order_created_at"] == 0
        assert fill["order_age_ms"] == 1_000
        assert fill["market_mid"] == 100.0
        assert fill["order_status"] == "ACTIVE"

    def test_order_activation_is_audited_when_latency_elapses(self):
        engine = ShadowQuoteEngine(assumptions=ExecutionAssumptions(latency_ms=100))
        engine.update_quotes(make_quotes(99.0, 101.0), timestamp=1_000)
        event_types = [event["event_type"] for event in engine.events]
        assert event_types[:2] == ["ORDER_CREATED", "ORDER_WAITING"]
        assert event_types[2:4] == ["ORDER_CREATED", "ORDER_WAITING"]
        engine.on_trade(make_trade(price=99.0, qty=0.01, is_buyer_maker=True, trade_id=2,))
        assert any(event["event_type"] == "ORDER_ACTIVE" for event in engine.events)
        assert any(event["event_type"] == "TRADE_RECEIVED" for event in engine.events)

    def test_quote_decision_event_contains_market_context(self):
        engine = ShadowQuoteEngine()
        state = engine.inventory._last_state = None
        from lobx.strategy.base import MarketState
        state = MarketState.from_book(
            timestamp=2_000,
            best_bid=99.0,
            best_bid_qty=2.0,
            best_ask=101.0,
            best_ask_qty=1.0,
            volatility=0.02,
            inventory=0.5,
        )
        engine.record_quote_decision(
            strategy="AS",
            state=state,
            quotes=make_quotes(98.0, 102.0),
        )
        event = engine.events[-1]
        assert event["event_type"] == "QUOTE_CALCULATED"
        assert event["market_mid"] == pytest.approx(100.0)
        assert event["market_bid"] == pytest.approx(99.0)
        assert event["market_ask"] == pytest.approx(101.0)
        assert event["spread"] == pytest.approx(2.0)
        assert event["inventory"] == pytest.approx(0.5)


# ── Task 4.11 regression tests — parameter isolation & quote sensitivity ──────

class TestParameterIsolation:
    """Regression: materially different gamma/kappa must produce different AS quotes.

    These tests would have caught the original identical-PnL bug where sweep
    runs were not receiving distinct parameter values.
    """

    def _make_state(self):
        from lobx.strategy.base import MarketState
        return MarketState.from_book(
            timestamp=1_000,
            best_bid=63999.9,
            best_bid_qty=1.0,
            best_ask=64000.1,
            best_ask_qty=1.0,
            volatility=1.5,
            inventory=0.0,
        )

    def test_different_kappa_produces_different_spread(self):
        """High kappa -> narrow spread. Low kappa -> wide spread."""
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
        state = self._make_state()
        strat_low_k = AvellanedaStoikovStrategy(gamma=1.0, kappa=0.5, qty=0.001)
        strat_high_k = AvellanedaStoikovStrategy(gamma=1.0, kappa=50.0, qty=0.001)
        q_low = strat_low_k.on_tick(state)
        q_high = strat_high_k.on_tick(state)
        spread_low_k = q_low.ask.price - q_low.bid.price
        spread_high_k = q_high.ask.price - q_high.bid.price
        assert spread_low_k > spread_high_k, (
            f"Low kappa ({spread_low_k:.4f}) should produce wider spread "
            f"than high kappa ({spread_high_k:.4f})"
        )

    def test_different_gamma_produces_different_bids_at_nonzero_inventory(self):
        """High gamma + long inventory -> lower reservation price -> lower bid."""
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
        from lobx.strategy.base import MarketState
        state = MarketState.from_book(
            timestamp=1_000,
            best_bid=63999.9,
            best_bid_qty=1.0,
            best_ask=64000.1,
            best_ask_qty=1.0,
            volatility=1.5,
            inventory=5.0,  # Long 5 BTC
        )
        strat_low_g = AvellanedaStoikovStrategy(gamma=0.01, kappa=1.0, qty=0.001)
        strat_high_g = AvellanedaStoikovStrategy(gamma=5.0, kappa=1.0, qty=0.001)
        q_low = strat_low_g.on_tick(state)
        q_high = strat_high_g.on_tick(state)
        assert q_high.bid.price < q_low.bid.price, (
            f"High gamma bid ({q_high.bid.price:.2f}) should be lower "
            f"than low gamma bid ({q_low.bid.price:.2f}) when long"
        )

    def test_extreme_combos_produce_distinct_bids_and_spreads(self):
        """5 extreme gamma/kappa combinations must all produce distinct bid prices."""
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
        state = self._make_state()
        combos = [(0.01, 0.5), (0.01, 50.0), (5.0, 0.5), (5.0, 50.0), (1.0, 10.0)]
        bids = []
        spreads = []
        for gamma, kappa in combos:
            strat = AvellanedaStoikovStrategy(gamma=gamma, kappa=kappa, qty=0.001)
            q = strat.on_tick(state)
            bids.append(q.bid.price)
            spreads.append(q.ask.price - q.bid.price)
        assert len(set(bids)) == len(combos), (
            f"All 5 extreme combos must produce distinct bids. Got: {bids}"
        )
        assert len(set(spreads)) == len(combos), (
            f"All 5 extreme combos must produce distinct spreads. Got: {spreads}"
        )

    def test_strategy_state_isolated_between_instances(self):
        """No shared mutable singleton leaks state between strategy instances."""
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
        state = self._make_state()
        s1 = AvellanedaStoikovStrategy(gamma=5.0, kappa=0.5, qty=0.001)
        s2 = AvellanedaStoikovStrategy(gamma=0.01, kappa=50.0, qty=0.001)
        q1 = s1.on_tick(state)
        _ = s2.on_tick(state)  # Run s2 — must not contaminate s1's params
        s1_fresh = AvellanedaStoikovStrategy(gamma=5.0, kappa=0.5, qty=0.001)
        q1_fresh = s1_fresh.on_tick(state)
        assert abs(q1.bid.price - q1_fresh.bid.price) < 1e-9, (
            "Re-running gamma=5.0,kappa=0.5 should be deterministic. "
            "State leaked from another strategy instance."
        )

    def test_gamma_kappa_stored_not_overridden_by_defaults(self):
        """gamma/kappa set in __init__ must not be overridden by any default."""
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
        for gamma, kappa in [(0.01, 0.5), (5.0, 50.0), (1.0, 10.0)]:
            strat = AvellanedaStoikovStrategy(gamma=gamma, kappa=kappa, qty=0.001)
            assert strat.gamma == gamma, f"gamma={gamma} overridden to {strat.gamma}"
            assert strat.kappa == kappa, f"kappa={kappa} overridden to {strat.kappa}"

    def test_audit_trail_reflects_configured_gamma_kappa(self):
        """last_quote_audit must contain the configured gamma/kappa, not class defaults."""
        from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
        state = self._make_state()
        for gamma, kappa in [(0.01, 0.5), (5.0, 50.0)]:
            strat = AvellanedaStoikovStrategy(gamma=gamma, kappa=kappa, qty=0.001)
            strat.on_tick(state)
            audit = strat.last_quote_audit
            assert audit.get("gamma") == gamma, (
                f"Audit gamma={audit.get('gamma')} != configured {gamma}"
            )
            assert audit.get("kappa") == kappa, (
                f"Audit kappa={audit.get('kappa')} != configured {kappa}"
            )
