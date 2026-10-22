"""Units and signs of the quoting models, volatility and Hawkes estimation."""
import math

import numpy as np
import pytest

from lobx.analytics.volatility import EWMAVolatility, realized_sigma
from lobx.models.avellaneda.optimal_spread import optimal_spread
from lobx.models.avellaneda.reservation_price import reservation_price
from lobx.models.glft import glft_coefficients, glft_depths
from lobx.models.hawkes.calibration import calibrate as hawkes_mle
from lobx.models.hawkes.intensity import HawkesIntensity, regime


def brownian_mids(sigma: float, seconds: int, seed: int = 1, s0: float = 70_000.0):
    """Mid path dS = sigma dW observed at irregular times (5-400 ms apart)."""
    rng = np.random.default_rng(seed)
    gaps = rng.integers(5, 400, size=seconds * 10)
    ts = np.cumsum(gaps).astype(np.int64)
    ts = ts[ts < seconds * 1000]
    dt = np.diff(ts, prepend=0) / 1000.0
    mids = s0 + np.cumsum(rng.normal(0.0, sigma * np.sqrt(dt)))
    return ts, mids


def test_ewma_sigma_is_in_usd_per_sqrt_second_and_independent_of_update_rate():
    ts, mids = brownian_mids(sigma=2.0, seconds=4000)
    est = EWMAVolatility(sample_s=1.0, halflife_s=600.0)
    for t, m in zip(ts.tolist(), mids.tolist()):
        est.update(t, m)
    assert est.is_warm
    assert est.sigma == pytest.approx(2.0, rel=0.15)
    assert realized_sigma(ts, mids) == pytest.approx(2.0, rel=0.05)


def test_ewma_seed_and_floor():
    est = EWMAVolatility(floor=0.5)
    assert est.sigma == 0.5
    est.seed(3.0)
    assert est.sigma == pytest.approx(3.0)


def test_as_reservation_price_moves_against_inventory_with_correct_magnitude():
    mid, gamma, sigma, tau = 70_000.0, 0.01, 2.0, 60.0
    assert reservation_price(mid, 0.0, gamma, sigma, tau) == mid
    r_long = reservation_price(mid, 3.0, gamma, sigma, tau)
    assert r_long == pytest.approx(mid - 3.0 * gamma * sigma**2 * tau)  # 7.2 USD below
    assert reservation_price(mid, -3.0, gamma, sigma, tau) == pytest.approx(mid + 7.2)


def test_as_spread_has_risk_and_liquidity_terms():
    gamma, sigma, tau, kappa = 0.01, 2.0, 60.0, 1.0
    expected = gamma * sigma**2 * tau + 2.0 / gamma * math.log1p(gamma / kappa)
    assert optimal_spread(gamma, sigma, tau, kappa) == pytest.approx(expected)
    # sigma matters: doubling it must widen the spread by the risk term
    assert optimal_spread(gamma, 2 * sigma, tau, kappa) > optimal_spread(gamma, sigma, tau, kappa)


def test_glft_coefficients_closed_form_and_limits():
    gamma, k, A = 0.05, 1.0, 0.5
    c = glft_coefficients(gamma, k, A)
    x = gamma / k
    assert c.c1 == pytest.approx(math.log1p(x) / gamma)
    assert c.c2 == pytest.approx(math.sqrt(gamma / (2 * A * k) * (1 + x) ** (1 / x + 1)))
    # gamma -> 0: c1 -> 1/k (pure liquidity half-spread)
    assert glft_coefficients(1e-9, k, A).c1 == pytest.approx(1.0 / k, rel=1e-6)


def test_glft_skew_sells_long_inventory_and_is_symmetric_when_flat():
    c = glft_coefficients(0.05, 1.0, 0.5)
    bid0, ask0, hs, skew = glft_depths(2.0, 0.0, c)
    assert bid0 == ask0 == hs
    bid_long, ask_long, _, _ = glft_depths(2.0, 2.0, c)
    assert bid_long > bid0 and ask_long < ask0  # long: bid further, ask closer
    assert bid_long - bid0 == pytest.approx(2.0 * skew)
    bid_short, ask_short, _, _ = glft_depths(2.0, -2.0, c)
    assert bid_short < bid0 and ask_short > ask0


def test_hawkes_intensity_decay_and_ratio():
    h = HawkesIntensity(mu=1.0, alpha=0.5, beta=2.0)
    h.update(10.0)
    assert h.evaluate(10.0) == pytest.approx(1.5)
    assert h.evaluate(11.0) == pytest.approx(1.0 + 0.5 * math.exp(-2.0))
    assert h.stationary_intensity == pytest.approx(1.0 / (1.0 - 0.25))
    assert h.ratio(10.0) == pytest.approx(1.5 * 0.75)
    with pytest.raises(ValueError):
        HawkesIntensity(mu=1.0, alpha=2.0, beta=2.0)


@pytest.mark.parametrize("ratio,label", [(0.5, "QUIET"), (1.0, "NORMAL"), (2.0, "ELEVATED"),
                                         (3.0, "BURST")])
def test_regime_bands(ratio, label):
    assert regime(ratio) == label


def test_hawkes_mle_recovers_simulated_parameters():
    true = HawkesIntensity(mu=0.8, alpha=1.2, beta=3.0)
    events = true.simulate(T=4000.0, seed=3)
    fit = hawkes_mle(events, T=4000.0)
    assert fit["branching_ratio"] == pytest.approx(0.4, abs=0.06)
    assert fit["mu"] == pytest.approx(0.8, rel=0.15)
    assert fit["beta"] == pytest.approx(3.0, rel=0.25)
    assert fit["mean_intensity"] == pytest.approx(fit["empirical_rate"], rel=0.05)
