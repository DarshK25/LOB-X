"""Unit tests for AS parameter calibration."""
import pytest
import numpy as np
import pandas as pd
from lobx.analytics.calibration import (
    CALIBRATION_ARTIFACT_SCHEMA,
    CalibrationArtifactError,
    calibrate_sigma,
    calibrate_kappa,
    calibrate_gamma,
    gamma_for_target_skew,
    load_calibration_artifact,
    write_calibration_artifact,
)
from lobx.models.avellaneda.model import to_usd_sigma


def test_calibrate_sigma():
    # Construct a synthetic DataFrame
    # Three log returns over one second each.
    df = pd.DataFrame({
        'event_time': [1000, 2000, 3000, 4000], # ms
        'price': [100.0, 101.0, 100.0, 101.0]
    })
    
    sigma = calibrate_sigma(df)
    assert not np.isnan(sigma)
    assert sigma > 0.0
    oracle = np.diff(np.log([100.0, 101.0, 100.0, 101.0]))
    assert sigma == pytest.approx(np.std(oracle))
    
    # Empty or 1 row
    assert calibrate_sigma(pd.DataFrame({'event_time': [1000], 'price': [100.0]})) == 0.0


def test_calibrate_kappa():
    # 4 trades over 3 seconds -> arrival rate ~ 1.33 / sec
    df = pd.DataFrame({
        'event_time': [1000, 2000, 3000, 4000], # ms
    })
    
    kappa = calibrate_kappa(df)
    assert not np.isnan(kappa)
    assert kappa > 0.0
    # Check default bounds
    assert 0.1 <= kappa <= 10.0


def test_calibrate_gamma():
    # baseline = 1 / (max_inv * sigma)
    gamma = calibrate_gamma(sigma=1.0, max_inventory=10.0, risk_tolerance=0.5)
    # modifier for 0.5 = 2.0 - 1.5(0.5) = 1.25
    # baseline = 1/10 = 0.1
    # expected = 0.125
    assert gamma == pytest.approx(0.125)
    
    # higher risk tolerance -> lower gamma
    gamma_risky = calibrate_gamma(sigma=1.0, max_inventory=10.0, risk_tolerance=1.0)
    assert gamma_risky < gamma
    
    # Edge cases
    assert calibrate_gamma(sigma=0.0, max_inventory=10.0) == 0.1


def test_gamma_for_target_skew_matches_independent_reservation_oracle():
    gamma = gamma_for_target_skew(
        target_ticks=4.0,
        inventory_limit=0.25,
        sigma_usd=2.0,
        tau=10.0,
        tick=0.5,
    )
    reservation_shift = 0.25 * gamma * (2.0 ** 2) * 10.0
    assert gamma == pytest.approx(0.1)
    assert reservation_shift == pytest.approx(4.0 * 0.5)


def test_to_usd_sigma_matches_price_return_oracle():
    assert to_usd_sigma(0.0025, 40_000.0) == pytest.approx(100.0)


def test_calibration_artifact_round_trip_records_provenance_and_fit_quality(tmp_path):
    depth = tmp_path / "depth.parquet"
    trades = tmp_path / "trades.parquet"
    depth.write_bytes(b"depth-source")
    trades.write_bytes(b"trade-source")
    artifact_path = tmp_path / "calibration-v1.json"

    written = write_calibration_artifact(
        artifact_path,
        sigma=0.0123,
        kappa=2.5,
        source_files={"depth": depth, "trades": trades},
        units={"kappa": "1/USD"},
        bucket_count=12,
        r_squared=0.91,
        p_value=0.004,
        bootstrap_ci_lower=1.0,
        bootstrap_ci_upper=2.0,
        bootstrap_reps=1000,
        valid=True,
        usable=True,
    )
    loaded = load_calibration_artifact(artifact_path, require_usable=True)

    assert loaded.schema == CALIBRATION_ARTIFACT_SCHEMA
    assert loaded.schema_version == 1
    assert loaded.source_hashes == written.source_hashes
    assert loaded.units["sigma"] == "log_return_per_sqrt_second"
    assert loaded.bucket_count == 12
    assert loaded.r_squared == pytest.approx(0.91)
    assert loaded.p_value == pytest.approx(0.004)
    assert loaded.sigma == pytest.approx(0.0123)
    assert loaded.kappa == pytest.approx(2.5)
    assert loaded.valid and loaded.usable


def test_unusable_calibration_artifact_is_not_loadable_for_strategy(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    artifact_path = tmp_path / "unusable.json"
    write_calibration_artifact(
        artifact_path,
        sigma=0.01,
        kappa=1.0,
        source_files={"capture": source},
        bucket_count=3,
        r_squared=0.1,
        p_value=0.9,
        valid=True,
        usable=False,
        validity_reason="R-squared below usability gate",
    )

    with pytest.raises(CalibrationArtifactError, match="not usable"):
        load_calibration_artifact(artifact_path, require_usable=True)


def test_strict_gate_rejects_four_bucket_artifact(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    with pytest.raises(CalibrationArtifactError, match="8 calibration buckets"):
        write_calibration_artifact(
            tmp_path / "strict.json",
            sigma=0.01,
            kappa=1.0,
            source_files={"capture": source},
            bucket_count=4,
            r_squared=0.9,
            p_value=0.001,
            bootstrap_ci_lower=0.5,
            bootstrap_ci_upper=1.0,
            usable=True,
        )
