"""Focused proofs for Hawkes process diagnostics."""
from lobx.analytics.hawkes import estimate_hawkes_diagnostics, exponential_intensity


def test_hawkes_intensity_increases_after_recent_events():
    times = [0, 100, 200, 5_000]
    intensity = exponential_intensity(times, mu=1.0, alpha=2.0, beta=1.0)
    assert intensity[1] > intensity[0]
    assert intensity[2] > intensity[3]
    diagnostics = estimate_hawkes_diagnostics(times)
    assert diagnostics.beta > 0
    assert 0 <= diagnostics.branching_ratio < 1
