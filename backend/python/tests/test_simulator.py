"""Paper exchange realism: latency, post-only, queue position, cancels, fees, limits."""
import pytest

from lobx.execution.queue_model import advance_on_depth_change
from lobx.execution.simulator import ASK, BID, ExecutionConfig, MarketSimulator, SimAccount
from lobx.strategy.base import NO_QUOTES, MarketState, Quote, QuotePair, Strategy
from lobx.strategy.features import FeatureEngine


class Fixed(Strategy):
    """Quotes given prices (None = no quote) regardless of state."""

    def __init__(self, bid=None, ask=None, qty=1.0):
        self.bid, self.ask, self.qty = bid, ask, qty

    def on_tick(self, state, position):
        return QuotePair(Quote(self.bid, self.qty) if self.bid else None,
                         Quote(self.ask, self.qty) if self.ask else None)


def cfg(**kw):
    base = dict(tick_size=1.0, maker_fee=0.0, taker_fee=0.0, feed_latency_ms=5,
                order_latency_ms=5, min_requote_ms=0, max_position=100.0)
    base.update(kw)
    return ExecutionConfig(**base)


def state(ts, bid=99.0, ask=101.0, bq=10.0, aq=10.0):
    return MarketState(ts, bid, ask, bq, aq, tick_size=1.0)


def live_account(strategy, config, bid=99.0, ask=101.0, bq=10.0, aq=10.0):
    acc = SimAccount(strategy, config)
    acc.decide(state(0, bid, ask, bq, aq))
    acc.advance(10, bid, bq, ask, aq)
    acc.on_book(10, bid, bq, ask, aq)
    return acc


def test_order_is_not_live_before_round_trip_latency():
    acc = SimAccount(Fixed(bid=100.0), cfg())
    acc.decide(state(0))
    acc.advance(9, 99.0, 10.0, 101.0, 10.0)
    assert acc.live == [] and len(acc.pending) == 1
    acc.advance(10, 99.0, 10.0, 101.0, 10.0)
    assert len(acc.live) == 1


def test_post_only_rejects_an_order_that_would_cross_on_arrival():
    acc = SimAccount(Fixed(bid=100.0), cfg())
    acc.decide(state(0))
    acc.advance(10, 99.0, 10.0, 100.0, 10.0)  # ask moved down to our bid in flight
    assert acc.rejects == 1 and acc.live == []


def test_order_better_than_touch_is_first_in_queue():
    acc = live_account(Fixed(bid=100.0), cfg())
    assert acc.live[0].front == 0.0
    acc.on_trade(20, 100.0, 0.4, True)
    assert acc.position == pytest.approx(0.4)


def test_order_joining_the_touch_waits_behind_displayed_size():
    acc = live_account(Fixed(bid=99.0), cfg(), bq=5.0)
    assert acc.live[0].front == 5.0
    acc.on_trade(20, 99.0, 3.0, True)  # consumes queue ahead only
    assert acc.position == 0.0
    acc.on_trade(21, 99.0, 2.5, True)  # 2 more ahead, then 0.5 for us
    assert acc.position == pytest.approx(0.5)


def test_optimistic_queue_fills_immediately_at_our_price():
    acc = live_account(Fixed(bid=99.0), cfg(queue_model="optimistic"), bq=5.0)
    acc.on_trade(20, 99.0, 0.5, True)
    assert acc.position == pytest.approx(0.5)


def test_trade_through_fills_regardless_of_queue():
    acc = live_account(Fixed(bid=99.0), cfg(), bq=50.0)
    acc.on_trade(20, 98.0, 2.0, True)
    assert acc.position == pytest.approx(1.0)  # order size caps the fill


def test_buy_aggressor_never_fills_our_bid():
    acc = live_account(Fixed(bid=100.0), cfg())
    acc.on_trade(20, 99.0, 5.0, False)
    assert acc.position == 0.0


def test_book_crossing_our_resting_price_fills_us():
    acc = live_account(Fixed(ask=100.0), cfg())
    acc.on_book(20, 100.0, 3.0, 101.0, 4.0)
    assert acc.position == pytest.approx(-1.0)


def test_cancel_in_flight_can_still_be_filled():
    acc = live_account(Fixed(bid=100.0), cfg())
    acc.cancel_all(20)  # effective at 30
    acc.advance(25, 99.0, 10.0, 101.0, 10.0)
    acc.on_trade(25, 100.0, 1.0, True)
    assert acc.position == pytest.approx(1.0)


def test_cancel_takes_effect_after_latency():
    acc = live_account(Fixed(bid=100.0), cfg())
    acc.cancel_all(20)
    acc.advance(30, 99.0, 10.0, 101.0, 10.0)
    acc.on_trade(30, 100.0, 1.0, True)
    assert acc.position == 0.0 and acc.live == []


def test_maker_rebate_and_taker_fee_accounting():
    acc = live_account(Fixed(bid=100.0), cfg(maker_fee=-0.0001, taker_fee=0.0005))
    acc.on_trade(20, 100.0, 1.0, True)
    assert acc.fees == pytest.approx(-0.01)
    acc.liquidate(30, 102.0, 103.0)
    assert acc.position == 0.0
    assert acc.fees == pytest.approx(-0.01 + 102.0 * 0.0005)
    assert acc.equity_at(102.0) == pytest.approx(2.0 - acc.fees)


def test_position_limit_blocks_the_side_that_grows_inventory():
    acc = SimAccount(Fixed(bid=98.0, ask=102.0), cfg(max_position=1.0))
    acc.position = 1.0
    acc.decide(state(0))
    sides = {o.side for o in acc.pending}
    assert sides == {ASK}


def test_drawdown_kill_switch_liquidates():
    acc = live_account(Fixed(bid=100.0), cfg(max_drawdown=1.0))
    acc.on_trade(20, 100.0, 1.0, True)
    acc.mark(30, 97.0, 98.0)
    assert acc.halted and acc.position == 0.0


def test_requote_only_when_price_moves_a_tick():
    strat = Fixed(bid=100.0)
    acc = live_account(strat, cfg())
    acc.decide(state(20))
    assert acc.orders_sent == 1
    strat.bid = 99.0
    acc.decide(state(30))
    assert acc.orders_sent == 2 and acc.cancels_sent == 1


def test_queue_model_cancels_mostly_ahead_when_at_back():
    # at the back of a 10-lot queue, a 4-lot cancel mostly happens ahead of us
    front = advance_on_depth_change(front=10.0, prev_qty=11.0, new_qty=7.0, cum_trade_qty=0.0)
    assert front < 10.0 - 2.0
    # at the front, cancellations are mostly behind us
    front2 = advance_on_depth_change(front=1.0, prev_qty=11.0, new_qty=7.0, cum_trade_qty=0.0)
    assert front2 == pytest.approx(1.0, abs=0.05)
    # increases join behind us; traded size is not double counted
    assert advance_on_depth_change(5.0, 10.0, 15.0, 0.0) == 5.0
    assert advance_on_depth_change(5.0, 10.0, 8.0, 2.0) == 5.0


def test_market_simulator_runs_all_accounts_on_the_same_events():
    feats = FeatureEngine(tick_size=1.0)
    a, b = SimAccount(Fixed(bid=100.0), cfg(), "A"), SimAccount(Fixed(), cfg(), "B")
    sim = MarketSimulator(feats, [a, b], decision_interval_ms=10)
    sim.on_book(1_000, 99.0, 10.0, 101.0, 10.0)
    sim.on_book(1_020, 99.0, 10.0, 101.0, 10.0)
    sim.on_trade(1_030, 100.0, 1.0, True)
    sim.on_book(2_100, 99.0, 10.0, 101.0, 10.0)
    sim.finish(flatten=True)
    assert a.position == 0.0 and len(a.fills) == 2  # maker buy, taker flatten
    assert b.fills == []
    assert len(a.equity) == len(b.equity) == len(sim.sample_times)


def test_no_quotes_strategy_never_trades():
    class Off(Strategy):
        def on_tick(self, s, p):
            return NO_QUOTES

    acc = SimAccount(Off(), cfg())
    acc.decide(state(0))
    assert acc.pending == [] and acc.orders_sent == 0
