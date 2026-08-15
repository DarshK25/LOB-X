"""Unit tests for AS parameter calibration."""
import pytest
import numpy as np
import pandas as pd
from lobx.analytics.calibration import calibrate_sigma, calibrate_kappa, calibrate_gamma


def test_calibrate_sigma():
    # Construct a synthetic DataFrame
    # 3 price changes of +1, -1, +1 over 1 second each
    df = pd.DataFrame({
        'event_time': [1000, 2000, 3000, 4000], # ms
        'price': [100.0, 101.0, 100.0, 101.0]
    })
    
    # dt = 1s, 1s, 1s
    # dp = 1.0, -1.0, 1.0
    # scaled_dp = dp / sqrt(dt) = 1.0, -1.0, 1.0
    # mean(scaled_dp) = 1/3, variance = sum((x - mu)^2) / 3 
    # (1 - 1/3)^2 = 4/9
    # (-1 - 1/3)^2 = 16/9
    # (1 - 1/3)^2 = 4/9
    # sum = 24/9 = 8/3, var = 8/9, std = sqrt(8)/3 ~ 0.9428
    
    sigma = calibrate_sigma(df)
    assert not np.isnan(sigma)
    assert sigma > 0.0
    assert sigma == pytest.approx(np.std([1.0, -1.0, 1.0]))
    
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
