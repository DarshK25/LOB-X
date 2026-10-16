"""Proof tests for adverse selection, volatility, and regimes."""
import pandas as pd
import pytest

from lobx.analytics.adverse_selection import (
    classify_fill_markouts,
    classify_markout,
    summarise_adverse_selection,
)
from lobx.analytics.regimes import RegimeConfig, classify_regimes, sample_market_states
from lobx.analytics.volatility import compare_volatility_estimators


def test_signed_markouts_classify_fill_outcomes():
    assert classify_markout(0.01) == "GOOD FILL"
    assert classify_markout(0.0) == "NEUTRAL FILL"
    assert classify_markout(-0.01) == "ADVERSE FILL"
    data = classify_fill_markouts([
        {"markout": -1.0, "available": True},
        {"markout": 1.0, "available": True},
        {"markout": None, "available": False},
    ])
    assert data.classification.tolist() == ["ADVERSE FILL", "GOOD FILL", "UNAVAILABLE"]
    assert summarise_adverse_selection(data)["adverse_fill_rate"] == pytest.approx(0.5)


def test_volatility_comparison_uses_same_price_path():
    result = compare_volatility_estimators([100.0, 101.0, 100.0, 102.0], window=2)
    assert list(result.columns) == ["observation", "rolling", "ewma", "realized"]
    assert len(result) == 4
    assert result["realized"].iloc[-1] > 0
    assert result["rolling"].iloc[-1] > 0


def test_regime_classifier_uses_observable_features():
    states = pd.DataFrame({
        "volatility_bps": [1.0, 2.0, 100.0],
        "spread": [1.0, 1.0, 1.0],
        "imbalance": [0.0, 0.0, 0.9],
        "trade_intensity": [0.0, 1.0, 10.0],
        "price_movement_bps": [0.0, 10.0, 100.0],
    })
    labels = classify_regimes(states, RegimeConfig())
    assert labels.iloc[2] == "TOXIC"
    assert set(labels).issubset({"CALM", "NORMAL", "VOLATILE", "TRENDING", "TOXIC"})


def test_sampled_market_states_include_imbalance_and_regime():
    rows = []
    for index in range(5):
        rows.extend([
            (index * 1_000, "bid", 100.0 + index, 2.0),
            (index * 1_000, "ask", 102.0 + index, 1.0),
        ])
    depth = pd.DataFrame(rows, columns=["event_time", "side", "price", "qty"])
    states = sample_market_states(depth, RegimeConfig(volatility_window=2))
    assert {"imbalance", "trade_intensity", "regime"}.issubset(states.columns)
