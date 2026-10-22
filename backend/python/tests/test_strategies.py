"""Strategy quoting: tick rounding, passivity, inventory skew direction, Hawkes and fees."""
import pytest

from lobx.strategy import (
    AvellanedaStoikovStrategy,
    FairValueModel,
    FixedSpreadStrategy,
    GLFTStrategy,
    InventorySkewStrategy,
)
from lobx.strategy.base import MarketState, passive_pair
from lobx.strategy.features import FeatureEngine


def st(**kw):
    base = dict(timestamp=0, best_bid=70_000.0, best_ask=70_000.1, best_bid_qty=5.0,
                best_ask_qty=5.0, tick_size=0.1, sigma=2.0)
    base.update(kw)
    return MarketState(**base)


def test_passive_pair_rounds_outward_and_never_crosses():
    s = st()
    pair = passive_pair(s, 70_000.27, 69_999.93, 1, 1)
    assert pair.bid.price == pytest.approx(70_000.0)  # clamped below best ask
    assert pair.ask.price == pytest.approx(70_000.1)  # clamped above best bid
    assert pair.bid.price < pair.ask.price


def test_fixed_spread_joins_the_touch():
    q = FixedSpreadStrategy(0.05, 0.001).on_tick(st(), 0.0)
    assert (q.bid.price, q.ask.price) == pytest.approx((70_000.0, 70_000.1))


@pytest.mark.parametrize("strategy", [
    InventorySkewStrategy(0.05, 0.001, skew_per_lot=0.5),
    AvellanedaStoikovStrategy(gamma=0.01, kappa=1.0, horizon_s=60.0, qty=0.001),
    GLFTStrategy(gamma=0.01, A=0.5, k=1.0, qty=0.001),
])
def test_long_inventory_lowers_quotes_and_short_raises_them(strategy):
    flat = strategy.on_tick(st(), 0.0)
    long_ = strategy.on_tick(st(), 0.005)   # 5 lots long
    short = strategy.on_tick(st(), -0.005)
    assert long_.ask.price <= flat.ask.price and long_.bid.price <= flat.bid.price
    assert short.bid.price >= flat.bid.price and short.ask.price >= flat.ask.price
    assert (long_.ask.price < flat.ask.price) or (long_.bid.price < flat.bid.price)


def test_as_uses_lots_not_btc_for_inventory():
    s = AvellanedaStoikovStrategy(gamma=0.01, kappa=1.0, horizon_s=60.0, qty=0.001)
    s.on_tick(st(), 0.003)
    assert s.last_quote_audit["inventory_lots"] == pytest.approx(3.0)
    assert s.last_quote_audit["inventory_skew"] == pytest.approx(-3 * 0.01 * 4.0 * 60.0)


def test_glft_widens_with_hawkes_ratio():
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, qty=0.001, hawkes_widen=0.5)
    calm = g.on_tick(st(hawkes_ratio=1.0), 0.0)
    hot = g.on_tick(st(hawkes_ratio=3.0), 0.0)
    assert hot.ask.price - hot.bid.price > calm.ask.price - calm.bid.price


def test_glft_burst_quotes_only_the_inventory_reducing_side():
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, qty=0.001, hawkes_pause_ratio=3.0)
    long_burst = g.on_tick(st(hawkes_ratio=4.0), 0.002)
    assert long_burst.bid is None and long_burst.ask is not None
    flat_burst = g.on_tick(st(hawkes_ratio=4.0), 0.0)
    assert flat_burst.bid is None and flat_burst.ask is None


def test_fair_value_shifts_both_quotes():
    fv = FairValueModel(b_obi=1.0)  # alpha = obi * sigma
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, qty=0.001, fair_value=fv)
    neutral = g.on_tick(st(), 0.0)
    bid_heavy = g.on_tick(st(best_bid_qty=9.0, best_ask_qty=1.0), 0.0)
    assert bid_heavy.bid.price > neutral.bid.price
    assert bid_heavy.ask.price > neutral.ask.price


def test_fee_aware_glft_drops_quotes_that_cannot_cover_the_fee():
    # half-spread ~1 USD vs fee 2 bp of 70k = 14 USD: nothing is worth quoting
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, qty=0.001, maker_fee=0.0002, fee_aware=True)
    q = g.on_tick(st(), 0.0)
    assert q.bid is None and q.ask is None
    rebate = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, qty=0.001, maker_fee=-0.00005, fee_aware=True)
    q2 = rebate.on_tick(st(), 0.0)
    assert q2.bid is not None and q2.ask is not None


def test_volume_alpha_uses_all_trade_prints_but_hawkes_deduplicates_order():
    f = FeatureEngine(0.1)
    f.on_book(1_000, 99.9, 1.0, 100.1, 1.0)
    f.on_trade(1_001, 100.1, 2.0, False)
    f.on_trade(1_001, 100.2, 3.0, False)
    imbalance, toxicity = f.volume_signals(1_001)
    assert imbalance == pytest.approx(1.0)
    assert toxicity == pytest.approx(1.0)
    assert f.hawkes_buy.intensity == pytest.approx(1.3)


def test_asymmetric_hawkes_widens_the_side_exposed_to_flow():
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, hawkes_widen=0.5,
                     asymmetric_hawkes=True)
    g.on_tick(st(hawkes_ratio=3.0, flow_imbalance=0.8), 0.0)
    assert g.last_quote_audit["ask_widen"] > g.last_quote_audit["bid_widen"]


def test_directional_pause_removes_only_the_toxic_side():
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, hawkes_pause_ratio=3.0,
                     directional_pause=True)
    buy_burst = g.on_tick(st(hawkes_ratio=4.0, flow_imbalance=0.8), 0.0)
    assert buy_burst.bid is not None and buy_burst.ask is None
    sell_burst = g.on_tick(st(hawkes_ratio=4.0, flow_imbalance=-0.8), 0.0)
    assert sell_burst.bid is None and sell_burst.ask is not None


def test_fee_floor_places_quotes_beyond_cost_instead_of_stopping():
    s = st()
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, maker_fee=0.0002,
                     fee_floor=True, edge_margin_bps=0.5)
    q = g.on_tick(s, 0.0)
    required = s.mid * 2.5 / 1e4
    assert q.bid is not None and s.mid - q.bid.price >= required
    assert q.ask is not None and q.ask.price - s.mid >= required


def test_alpha_gate_pauses_adverse_side_but_preserves_inventory_reduction():
    fair = FairValueModel(b_obi=1.0)
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, fair_value=fair,
                     alpha_pause_sigma=0.1)
    rising = st(best_bid_qty=9.0, best_ask_qty=1.0)
    flat = g.on_tick(rising, 0.0)
    assert flat.bid is not None and flat.ask is None
    long_inventory = g.on_tick(rising, 0.001)
    assert long_inventory.ask is not None


def test_disabled_robust_gate_abstains():
    g = GLFTStrategy(gamma=0.01, A=0.5, k=1.0, enabled=False)
    q = g.on_tick(st(), 0.0)
    assert q.bid is None and q.ask is None
