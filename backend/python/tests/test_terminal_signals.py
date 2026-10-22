"""Live signals use observed books/trades, exchange time, and no synthetic arrivals.

Hawkes defaults before the first online refit: buy and sell processes each
mu=0.5, alpha=0.8, beta=2, so the baseline total is 1.0/s and the stationary
total is 2 * 0.5 / (1 - 0.4) = 5/3 per second.
"""
import math
from dataclasses import replace

import pytest

from lobx.market_data.trade_stream import TradeTick
from lobx.terminal.signals import LiveMarketSignals

EPOCH_MS = 1_700_000_000_000
STATIONARY = 5.0 / 3.0
ONE_BUY = 1.8  # 0.5 + 0.8 (buy) + 0.5 (sell)
ONE_BUY_AFTER_1S = 1.0 + 0.8 * math.exp(-2.0)
BOOK_FIELDS = (
    "mid", "spread", "spread_bps", "microprice", "microprice_offset", "imbalance",
)


def trade(trade_id=1, *, timestamp=EPOCH_MS, price=100.0, qty=1.0, buy=True):
    return TradeTick(
        event_time=timestamp,
        trade_id=trade_id,
        price=price,
        qty=qty,
        is_buyer_maker=not buy,
        symbol="BTCUSDT",
    )


def test_uninitialized_signals_do_not_invent_book_or_trade_values():
    state = LiveMarketSignals().snapshot(EPOCH_MS)

    assert all(getattr(state, name) is None for name in BOOK_FIELDS)
    assert state.book_timestamp is None
    assert state.last_trade_timestamp is None
    assert state.vwap is None
    assert state.total_volume == 0.0
    assert state.buy_volume == 0.0
    assert state.buy_fraction is None
    assert state.trade_count == 0
    assert state.hawkes_intensity == pytest.approx(1.0)
    assert state.hawkes_ratio == pytest.approx(1.0 / STATIONARY)
    assert state.hawkes_regime == "QUIET"
    assert state.flow_imbalance == 0.0
    assert state.hawkes_calibrated is False


def test_l1_spread_microprice_and_imbalance_follow_book_changes():
    signals = LiveMarketSignals()
    signals.on_book([(99.0, 3.0), (98.0, 500.0)], [(101.0, 1.0)], EPOCH_MS)
    first = signals.snapshot(EPOCH_MS)

    assert first.mid == 100.0
    assert first.spread == 2.0
    assert first.spread_bps == 200.0
    assert first.microprice == 100.5
    assert first.microprice_offset == 0.5
    assert first.imbalance == 0.5
    assert first.book_timestamp == EPOCH_MS

    signals.on_book([(98.0, 1.0)], [(102.0, 3.0), (103.0, 500.0)], EPOCH_MS + 250)
    second = signals.snapshot(EPOCH_MS + 250)
    assert second.mid == 100.0
    assert second.spread == 4.0
    assert second.spread_bps == 400.0
    assert second.microprice == 99.0
    assert second.microprice_offset == -1.0
    assert second.imbalance == -0.5
    assert second.book_timestamp == EPOCH_MS + 250
    assert first.microprice == 100.5  # Previously returned snapshots are detached.


@pytest.mark.parametrize(
    "bids,asks",
    [
        ([], []),
        ([(99.0, 1.0)], []),
        ([], [(101.0, 1.0)]),
        ([(102.0, 1.0)], [(101.0, 1.0)]),
        ([(101.0, 1.0)], [(101.0, 1.0)]),
        ([(0.0, 1.0)], [(101.0, 1.0)]),
        ([(99.0, 0.0)], [(101.0, 1.0)]),
        ([(99.0, 1.0)], [(101.0, -1.0)]),
        ([(math.nan, 1.0)], [(101.0, 1.0)]),
        ([(99.0, math.inf)], [(101.0, 1.0)]),
        ([(99.0, 1.0)], [(math.inf, 1.0)]),
        ([(99.0, 1.0), (98.0, -1.0)], [(101.0, 1.0)]),
        ([(99.0, 1.0), (100.0, 1.0)], [(101.0, 1.0)]),
        ([(99.0, 1.0)], [(101.0, 1.0), (100.0, 1.0)]),
    ],
)
def test_invalid_book_clears_previous_good_values(bids, asks):
    signals = LiveMarketSignals()
    signals.on_book([(99.0, 1.0)], [(101.0, 1.0)], EPOCH_MS)
    signals.on_book(bids, asks, EPOCH_MS + 1)

    state = signals.snapshot(EPOCH_MS + 1)
    assert all(getattr(state, name) is None for name in BOOK_FIELDS)
    assert state.book_timestamp == EPOCH_MS + 1

    signals.on_book([(99.0, 1.0)], [(101.0, 1.0)], EPOCH_MS + 2)
    assert signals.snapshot(EPOCH_MS + 2).mid == 100.0


def test_session_vwap_and_buy_fraction_are_quantity_weighted():
    signals = LiveMarketSignals()
    signals.on_trade(trade(1, price=100.0, qty=2.0))
    signals.on_trade(trade(2, price=110.0, qty=1.0, buy=False, timestamp=EPOCH_MS + 100))
    signals.on_trade(trade(3, price=90.0, qty=1.0, timestamp=EPOCH_MS + 200))

    state = signals.snapshot(EPOCH_MS + 200)
    assert state.trade_count == 3
    assert state.total_volume == 4.0
    assert state.buy_volume == 3.0
    assert state.buy_fraction == 0.75
    assert state.vwap == 100.0
    assert state.last_trade_timestamp == EPOCH_MS + 200
    assert state.mid is None  # Trades never stand in for an unavailable book.


def test_hawkes_decay_uses_seconds_and_snapshots_do_not_add_arrivals():
    signals = LiveMarketSignals()
    signals.on_trade(trade())
    immediate = signals.snapshot(EPOCH_MS)
    assert immediate.hawkes_intensity == pytest.approx(ONE_BUY)
    assert immediate.hawkes_ratio == pytest.approx(ONE_BUY / STATIONARY)
    assert immediate.hawkes_regime == "NORMAL"
    assert immediate.flow_imbalance == pytest.approx(0.8 / ONE_BUY)

    decayed = signals.snapshot(EPOCH_MS + 1_000)
    # One second, beta=2: decay exp(-2), not exp(-2000).
    assert decayed.hawkes_intensity == pytest.approx(ONE_BUY_AFTER_1S)
    assert decayed.hawkes_ratio == pytest.approx(ONE_BUY_AFTER_1S / STATIONARY)
    assert decayed.hawkes_regime == "QUIET"
    assert signals.snapshot(EPOCH_MS + 1_000) == decayed
    assert decayed.trade_count == 1
    assert signals.snapshot(EPOCH_MS + 60_000).hawkes_intensity == pytest.approx(1.0)


def test_burst_regime_from_a_cluster_of_buy_market_orders():
    signals = LiveMarketSignals()
    for i in range(6):
        signals.on_trade(trade(i, timestamp=EPOCH_MS + i))

    state = signals.snapshot(EPOCH_MS + 5)
    assert state.hawkes_ratio >= 3.0
    assert state.hawkes_regime == "BURST"
    assert state.flow_imbalance > 0.8


def test_prints_in_the_same_millisecond_are_one_market_order():
    signals = LiveMarketSignals()
    for trade_id in range(3):
        signals.on_trade(trade(trade_id))
    state = signals.snapshot(EPOCH_MS)
    assert state.trade_count == 3
    assert state.hawkes_intensity == pytest.approx(ONE_BUY)


def test_duplicate_trade_ids_are_ignored_even_if_payload_changes():
    signals = LiveMarketSignals()
    original = trade(7, qty=2.0)
    signals.on_trade(original)
    before = signals.snapshot(EPOCH_MS)
    signals.on_trade(original)
    signals.on_trade(replace(original, price=500.0, qty=8.0, event_time=EPOCH_MS + 100))

    assert signals.snapshot(EPOCH_MS) == before


def test_deduplication_is_bounded_fifo_not_unlimited_replay_protection():
    signals = LiveMarketSignals(dedup_capacity=2)
    for trade_id in (1, 2, 3):
        signals.on_trade(trade(trade_id))
    signals.on_trade(trade(2))  # Retained ID, ignored; does not refresh its age.
    assert signals.snapshot(EPOCH_MS).trade_count == 3

    signals.on_trade(trade(1))  # Evicted ID is accepted again, as documented.
    state = signals.snapshot(EPOCH_MS)
    assert state.trade_count == 4
    assert state.total_volume == 4.0
    assert state.hawkes_intensity == pytest.approx(ONE_BUY)
    signals.on_trade(trade(3))
    assert signals.snapshot(EPOCH_MS).trade_count == 4


def test_late_unique_trade_counts_in_totals_without_rewinding_hawkes():
    signals = LiveMarketSignals()
    signals.on_trade(trade(2, timestamp=EPOCH_MS + 1_000, price=110.0, qty=2.0))
    signals.on_trade(trade(1, timestamp=EPOCH_MS, price=80.0, qty=1.0, buy=False))
    state = signals.snapshot(EPOCH_MS + 1_000)

    assert state.total_volume == 3.0
    assert state.buy_volume == 2.0
    assert state.buy_fraction == pytest.approx(2 / 3)
    assert state.vwap == 100.0
    assert state.trade_count == 2
    assert state.last_trade_timestamp == EPOCH_MS + 1_000
    assert state.hawkes_intensity == pytest.approx(ONE_BUY)
    signals.on_trade(trade(1, timestamp=EPOCH_MS))
    assert signals.snapshot(EPOCH_MS + 1_000).trade_count == 2

    signals.on_trade(trade(3, timestamp=EPOCH_MS + 2_000))
    assert signals.snapshot(EPOCH_MS + 2_000).hawkes_intensity == pytest.approx(
        ONE_BUY_AFTER_1S + 0.8
    )


def test_clock_skew_and_backwards_snapshots_cannot_increase_intensity():
    signals = LiveMarketSignals()
    signals.on_trade(trade(timestamp=EPOCH_MS + 1_000))
    assert signals.snapshot(EPOCH_MS).hawkes_intensity == pytest.approx(ONE_BUY)
    later = signals.snapshot(EPOCH_MS + 2_000)
    backwards = signals.snapshot(EPOCH_MS - 60_000)
    assert backwards.hawkes_intensity == later.hawkes_intensity
    assert backwards.last_trade_timestamp == EPOCH_MS + 1_000


def test_trade_behind_snapshot_clock_decays_from_its_real_event_time():
    signals = LiveMarketSignals()
    signals.snapshot(EPOCH_MS + 1_000)
    signals.on_trade(trade(timestamp=EPOCH_MS))

    assert signals.snapshot(EPOCH_MS).hawkes_intensity == pytest.approx(ONE_BUY_AFTER_1S)
    assert signals.snapshot(EPOCH_MS).last_trade_timestamp == EPOCH_MS


@pytest.mark.parametrize("field", ["price", "qty", "event_time"])
@pytest.mark.parametrize("invalid", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_trade_numbers_are_ignored_without_reserving_the_id(field, invalid):
    signals = LiveMarketSignals()
    valid = trade(1)
    signals.on_trade(replace(valid, **{field: invalid}))
    empty = signals.snapshot(EPOCH_MS)
    assert empty.trade_count == 0
    assert empty.total_volume == 0.0
    assert empty.vwap is None
    assert empty.last_trade_timestamp is None
    assert empty.hawkes_intensity == pytest.approx(1.0)

    signals.on_trade(valid)
    assert signals.snapshot(EPOCH_MS).trade_count == 1


def test_unrepresentable_trade_totals_do_not_poison_session_state():
    signals = LiveMarketSignals()
    signals.on_trade(trade(1, price=1e308, qty=1e308))
    assert signals.snapshot(EPOCH_MS).trade_count == 0
    signals.on_trade(trade(1, price=1.0, qty=1e308))
    signals.on_trade(trade(2, price=1.0, qty=1e308))  # Aggregate overflow.
    state = signals.snapshot(EPOCH_MS)
    assert state.trade_count == 1
    assert state.total_volume == 1e308
    assert state.vwap == 1.0
    assert state.hawkes_intensity == pytest.approx(ONE_BUY)


@pytest.mark.parametrize(
    "changes",
    [
        {"trade_id": -1},
        {"trade_id": math.nan},
        {"trade_id": True},
        {"is_buyer_maker": "false"},
        {"symbol": ""},
    ],
)
def test_invalid_trade_identity_or_side_does_not_corrupt_session(changes):
    signals = LiveMarketSignals()
    signals.on_trade(replace(trade(), **changes))
    assert signals.snapshot(EPOCH_MS).trade_count == 0
    signals.on_trade(trade())
    assert signals.snapshot(EPOCH_MS).trade_count == 1


def test_a_session_does_not_mix_symbols():
    signals = LiveMarketSignals()
    signals.on_trade(trade())
    signals.on_trade(replace(trade(2, price=5_000.0), symbol="ETHUSDT"))
    state = signals.snapshot(EPOCH_MS)
    assert state.trade_count == 1
    assert state.vwap == 100.0


@pytest.mark.parametrize("invalid", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_book_timestamp_clears_the_book_and_timestamp(invalid):
    signals = LiveMarketSignals()
    signals.on_book([(99.0, 1.0)], [(101.0, 1.0)], EPOCH_MS)
    signals.on_book([(99.0, 1.0)], [(101.0, 1.0)], invalid)
    state = signals.snapshot(EPOCH_MS)
    assert all(getattr(state, name) is None for name in BOOK_FIELDS)
    assert state.book_timestamp is None


@pytest.mark.parametrize("invalid", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_snapshot_time_raises_without_mutating_state(invalid):
    signals = LiveMarketSignals()
    signals.on_trade(trade())
    with pytest.raises(ValueError):
        signals.snapshot(invalid)
    assert signals.snapshot(EPOCH_MS).hawkes_intensity == pytest.approx(ONE_BUY)


@pytest.mark.parametrize("invalid", [0, -1, 1.5, True])
def test_deduplication_capacity_must_be_a_positive_integer(invalid):
    with pytest.raises(ValueError):
        LiveMarketSignals(dedup_capacity=invalid)
