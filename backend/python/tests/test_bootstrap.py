"""Tests for session-level confidence intervals."""
from __future__ import annotations

import pytest

from lobx.analytics.bootstrap import bootstrap_mean_ci


def test_bootstrap_is_reproducible_and_contains_point_estimate():
    result = bootstrap_mean_ci([-1.0, 0.0, 1.0, 2.0], replications=500, seed=7)
    repeat = bootstrap_mean_ci([-1.0, 0.0, 1.0, 2.0], replications=500, seed=7)
    assert result == repeat
    assert result["estimate"] == pytest.approx(0.5)
    assert result["lower"] <= result["estimate"] <= result["upper"]


def test_bootstrap_reports_degenerate_and_empty_inputs_explicitly():
    assert bootstrap_mean_ci([2.0])["lower"] == pytest.approx(2.0)
    assert bootstrap_mean_ci([])["estimate"] is None
