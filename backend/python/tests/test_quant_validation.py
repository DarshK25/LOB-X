"""
tests/test_quant_validation.py — Unit tests for Milestone 4 Quant Validation tasks.
"""
from __future__ import annotations

import pandas as pd
import pytest

from lobx.analytics.adverse_selection import analyze_adverse_selection
from lobx.analytics.calibration import calibrate_kappa
from lobx.analytics.microprice import evaluate_microprice_predictive_power


def test_calibrate_kappa_returns_sensible_float():
    df_trades = pd.DataFrame([
        {"event_time": 1000, "price": 64000.0},
        {"event_time": 2000, "price": 64001.0},
        {"event_time": 3000, "price": 64000.5},
    ])
    kappa = calibrate_kappa(df_trades)
    assert isinstance(kappa, float)
    assert 0.1 <= kappa <= 10.0


def test_evaluate_microprice_predictive_power_structure():
    records = [
        {"timestamp": 1000, "mid": 100.0, "microprice_deviation": 0.5},
        {"timestamp": 1500, "mid": 100.2, "microprice_deviation": 0.3},
        {"timestamp": 2000, "mid": 100.5, "microprice_deviation": -0.2},
    ]
    res = evaluate_microprice_predictive_power(records, horizons_ms=(500, 1000))
    assert "count" in res
    assert "horizons" in res


def test_analyze_adverse_selection_returns_horizon_breakdown():
    markouts = [
        {"order_id": "1", "horizon_ms": 100, "markout": 0.5, "available": True, "classification": "GOOD FILL"},
        {"order_id": "1", "horizon_ms": 500, "markout": -0.2, "available": True, "classification": "ADVERSE FILL"},
    ]
    res = analyze_adverse_selection(markouts)
    assert "100ms" in res
    assert "500ms" in res
