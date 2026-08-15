"""tests/test_strategy.py — unit tests for Strategy implementations."""
import math
import pytest
from lobx.strategy.base import MarketState
from lobx.strategy.fixed_spread import FixedSpreadStrategy
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.no_trade import NoTradeStrategy
from lobx.strategy.inventory_skew import InventorySkewStrategy


def make_state(**overrides) -> MarketState:
    """Helper: build a MarketState with sensible defaults."""
    defaults = dict(
        timestamp=1_000_000,
        mid_price=100.0,
        best_bid=99.5,
        best_bid_qty=1.0,
        best_ask=100.5,
        best_ask_qty=1.0,
        spread=1.0,
        volatility=0.0001,
        inventory=0.0,
    )
    defaults.update(overrides)
    return MarketState(**defaults)


# ── FixedSpreadStrategy ───────────────────────────────────────────────────────

class TestFixedSpreadStrategy:

    def test_symmetric_around_mid(self):
        strat = FixedSpreadStrategy(half_spread=1.0, qty=0.01)
        state = make_state(mid_price=100.0)
        q = strat.on_tick(state)
        assert q.bid is not None
        assert q.ask is not None
        assert abs(q.bid.price - 99.0)  < 1e-9
        assert abs(q.ask.price - 101.0) < 1e-9

    def test_qty_correct(self):
        strat = FixedSpreadStrategy(half_spread=0.5, qty=0.002)
        state = make_state(mid_price=50000.0)
        q = strat.on_tick(state)
        assert q.bid.qty == 0.002
        assert q.ask.qty == 0.002

    def test_spread_is_2x_half_spread(self):
        strat = FixedSpreadStrategy(half_spread=2.5, qty=0.001)
        state = make_state(mid_price=200.0)
        q = strat.on_tick(state)
        spread = round(q.ask.price - q.bid.price, 8)
        assert abs(spread - 5.0) < 1e-6

    def test_quotes_move_with_mid(self):
        strat = FixedSpreadStrategy(half_spread=1.0, qty=0.001)
        s1 = strat.on_tick(make_state(mid_price=100.0))
        s2 = strat.on_tick(make_state(mid_price=200.0))
        assert s2.bid.price > s1.bid.price
        assert s2.ask.price > s1.ask.price

    def test_inventory_has_no_effect(self):
        """FixedSpread ignores inventory — both quotes should be identical."""
        strat = FixedSpreadStrategy(half_spread=1.0)
        q0 = strat.on_tick(make_state(mid_price=100.0, inventory=0.0))
        q5 = strat.on_tick(make_state(mid_price=100.0, inventory=5.0))
        assert abs(q0.bid.price - q5.bid.price) < 1e-9
        assert abs(q0.ask.price - q5.ask.price) < 1e-9

    def test_name(self):
        assert FixedSpreadStrategy().name() == "FixedSpreadStrategy"


# ── AvellanedaStoikovStrategy ─────────────────────────────────────────────────

class TestAvellanedaStoikovStrategy:

    def test_returns_valid_quote_pair(self):
        strat = AvellanedaStoikovStrategy(gamma=0.1, kappa=1.5)
        q = strat.on_tick(make_state())
        assert q.bid is not None
        assert q.ask is not None

    def test_bid_strictly_below_ask(self):
        strat = AvellanedaStoikovStrategy(gamma=0.1, kappa=1.5, min_spread=0.02)
        q = strat.on_tick(make_state())
        assert q.bid.price < q.ask.price

    def test_inventory_shifts_quotes(self):
        """Core AS property: long inventory → reservation price shifts DOWN.
        Use large gamma/sigma/inventory so the shift is visible after rounding.
        Shift = inventory * gamma * sigma^2 * T = 10 * 2.0 * 1.0^2 * 300 = 6000.
        That's large enough to see even after round(..., 2).
        """
        strat_neutral = AvellanedaStoikovStrategy(gamma=2.0, kappa=1.5, horizon_seconds=300.0)
        strat_long    = AvellanedaStoikovStrategy(gamma=2.0, kappa=1.5, horizon_seconds=300.0)

        # sigma=1.0 (e.g. $1 per tick vol), inventory=10 long
        q_neutral = strat_neutral.on_tick(make_state(
            mid_price=60000.0, inventory=0.0, volatility=1.0))
        q_long    = strat_long.on_tick(make_state(
            mid_price=60000.0, inventory=10.0, volatility=1.0))

        # With long inventory, reservation price < mid → quotes shift down.
        assert q_long.bid.price < q_neutral.bid.price, (
            f"Expected q_long.bid ({q_long.bid.price}) < q_neutral.bid ({q_neutral.bid.price})"
        )
        assert q_long.ask.price < q_neutral.ask.price

    def test_zero_volatility_uses_floor_not_crash(self):
        """Vol = 0 would make the AS formula degenerate — the floor prevents this."""
        strat = AvellanedaStoikovStrategy(gamma=0.1, kappa=1.5)
        q = strat.on_tick(make_state(volatility=0.0))
        assert q.bid is not None and q.ask is not None
        assert q.bid.price < q.ask.price

    def test_min_spread_enforced(self):
        """With very low vol, min_spread should kick in."""
        strat = AvellanedaStoikovStrategy(gamma=0.1, kappa=1.5, min_spread=5.0)
        q = strat.on_tick(make_state(volatility=0.0))
        assert (q.ask.price - q.bid.price) >= 5.0 - 1e-6

    def test_quotes_change_with_inventory(self):
        """Inventory must produce a measurable shift in the reservation price.
        Use large gamma/sigma so the shift survives rounding to 2dp.
        """
        strat = AvellanedaStoikovStrategy(gamma=2.0, kappa=1.5, horizon_seconds=300.0)
        q1 = strat.on_tick(make_state(mid_price=60000.0, inventory=0.0,  volatility=1.0))
        q2 = strat.on_tick(make_state(mid_price=60000.0, inventory=10.0, volatility=1.0))
        assert q1.bid.price != q2.bid.price, (
            f"Inventory must affect quotes: q1.bid={q1.bid.price}, q2.bid={q2.bid.price}"
        )

    def test_name(self):
        assert AvellanedaStoikovStrategy().name() == "AvellanedaStoikovStrategy"


def test_no_trade_strategy_never_quotes():
    quotes = NoTradeStrategy().on_tick(make_state())
    assert quotes.bid is None
    assert quotes.ask is None


def test_inventory_skew_moves_quotes_away_from_long_inventory():
    strategy = InventorySkewStrategy(half_spread=1.0, qty=0.001, skew_usd_per_btc=100.0)
    neutral = strategy.on_tick(make_state(mid_price=100.0, inventory=0.0))
    long = strategy.on_tick(make_state(mid_price=100.0, inventory=0.01))
    assert long.bid.price == neutral.bid.price - 1.0
    assert long.ask.price == neutral.ask.price - 1.0
