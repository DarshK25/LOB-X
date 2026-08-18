import pandas as pd
import pytest

from lobx.analytics.regimes import (
    RegimeConfig,
    attribute_fills_to_regimes,
    sample_market_states,
    summarise_regimes,
)


def _depth() -> pd.DataFrame:
    rows = []
    for index in range(6):
        timestamp = index * 1_000
        rows.extend(
            [
                (timestamp, "bid", 100.0 + index, 1.0 + index),
                # Keep asks well above the growing historical bid ladder;
                # raw L2 diffs retain old levels until an explicit deletion.
                (timestamp, "ask", 200.0 + index, 2.0 + index),
            ]
        )
    return pd.DataFrame(rows, columns=["event_time", "side", "price", "qty"])


def test_regime_attribution_uses_prior_state_and_future_mid():
    config = RegimeConfig(sample_interval_ms=1_000, volatility_window=2, markout_horizon_ms=1_000)
    states = sample_market_states(_depth(), config)
    fills = pd.DataFrame(
        [(1, "buy", 101.0, 0.01, 2_000, 0.001)],
        columns=["order_id", "side", "price", "qty", "timestamp", "fee"],
    )
    attributed = attribute_fills_to_regimes(fills, states, config)

    # At t=3s the mid is 151.5, so a 0.01 BTC buy at 101 has +0.505 USDT markout.
    assert attributed.iloc[0].markout_1s == pytest.approx(0.505)
    summary = summarise_regimes(attributed)
    assert summary.fills.sum() == 1
