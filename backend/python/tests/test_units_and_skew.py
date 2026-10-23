"""R-01 independent oracle and unit-conversion tests."""
from __future__ import annotations

import math

import pytest

from lobx.models.avellaneda.model import (
    calculate_quote,
    gamma_for_target_skew,
    to_usd_sigma,
)
from lobx.strategy.avellaneda import AvellanedaStoikovStrategy
from lobx.strategy.base import MarketState


MID, SIGMA_LOG = 64_000.0, 4.84e-6
SIGMA_USD = SIGMA_LOG * MID
GAMMA, KAPPA, TAU = 0.1, 63.6287, 300.0


def as_reference(mid, q, gamma, sigma_usd, tau, kappa):
    """Independent AS oracle; deliberately does not import project formulas."""
    reservation = mid - q * gamma * sigma_usd**2 * tau
    spread = gamma * sigma_usd**2 * tau + (2.0 / gamma) * math.log(1.0 + gamma / kappa)
    return reservation, spread


def _state(**overrides):
    values = dict(
        timestamp=1_000,
        best_bid=63_999.99,
        best_ask=64_000.01,
        mid_price=MID,
        spread=0.02,
        best_bid_qty=1.0,
        best_ask_qty=1.0,
        volatility=SIGMA_LOG,
        inventory=0.005,
    )
    values.update(overrides)
    return MarketState(**values)


def test_reference_sanity():
    reservation, spread = as_reference(MID, 0.005, GAMMA, SIGMA_USD, TAU, KAPPA)
    assert MID - reservation == pytest.approx(0.0144, abs=2e-4)
    assert spread == pytest.approx(2.910, abs=0.02)


def test_sigma_conversion_and_project_skew_match_oracle():
    assert to_usd_sigma(SIGMA_LOG, MID) == pytest.approx(SIGMA_USD)
    expected_reservation, expected_spread = as_reference(
        MID, 0.005, GAMMA, SIGMA_USD, TAU, KAPPA
    )
    quote = calculate_quote(MID, 0.005, GAMMA, SIGMA_USD, TAU, KAPPA)
    assert quote.reservation_price == pytest.approx(expected_reservation, abs=1e-12)
    assert quote.full_spread == pytest.approx(expected_spread, abs=1e-12)
    assert expected_reservation < MID
    print(
        "inventory_before | inventory_after | reservation_before | reservation_after | "
        "shift_usd | shift_ticks | expected_shift_usd"
    )
    print(
        f"0.000000 | 0.005000 | {MID:.8f} | {quote.reservation_price:.8f} | "
        f"{MID - quote.reservation_price:.8f} | {(MID - quote.reservation_price) / 0.01:.8f} | "
        f"{MID - expected_reservation:.8f}"
    )


def test_strategy_diagnostics_are_in_usd_model_units():
    strategy = AvellanedaStoikovStrategy(
        gamma=GAMMA,
        kappa=KAPPA,
        horizon_seconds=TAU,
        sigma_override=SIGMA_LOG,
    )
    strategy.on_tick(_state())
    audit = strategy.last_quote_audit
    assert audit["sigma_log"] == pytest.approx(SIGMA_LOG)
    assert audit["sigma_usd"] == pytest.approx(SIGMA_USD)
    assert audit["reservation_price"] == pytest.approx(
        as_reference(MID, 0.005, GAMMA, SIGMA_USD, TAU, KAPPA)[0]
    )
    assert audit["spread_total"] == pytest.approx(
        audit["variance_term"] + audit["kappa_term"]
    )


def test_gamma_for_target_skew():
    gamma = gamma_for_target_skew(2.0, 0.005, 0.3098, 300.0, 0.01)
    assert gamma == pytest.approx(0.139, rel=0.02)
