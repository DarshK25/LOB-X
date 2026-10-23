"""Runner wiring tests for explicit calibration artifacts."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from lobx.analytics.calibration import write_calibration_artifact

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_backtest  # noqa: E402
import run_live_strategy  # noqa: E402


def _args() -> SimpleNamespace:
    return SimpleNamespace(
        strategy="as",
        gamma=0.1,
        kappa=1.5,
        horizon=300.0,
        qty=0.001,
        min_spread=0.02,
    )


def test_explicit_artifact_parameters_are_used_by_both_runners(tmp_path):
    source = tmp_path / "capture.bin"
    source.write_bytes(b"capture")
    artifact = write_calibration_artifact(
        tmp_path / "calibration.json",
        sigma=0.02,
        kappa=3.0,
        source_files={"capture": source},
        bucket_count=8,
        r_squared=0.8,
        p_value=0.01,
        bootstrap_ci_lower=1.0,
        bootstrap_ci_upper=2.0,
        bootstrap_reps=1000,
        valid=True,
        usable=True,
    )
    args = _args()

    backtest_strategy = run_backtest._strategy_factory("as", args, artifact)()
    live_strategy = run_live_strategy.build_strategy(args, artifact)

    assert backtest_strategy.kappa == 3.0
    assert backtest_strategy.sigma_override == 0.02
    assert live_strategy.kappa == 3.0
    assert live_strategy.sigma_override == 0.02


def test_runners_keep_placeholder_parameters_without_artifact():
    args = _args()

    backtest_strategy = run_backtest._strategy_factory("as", args)()
    live_strategy = run_live_strategy.build_strategy(args)

    assert backtest_strategy.kappa == args.kappa
    assert backtest_strategy.sigma_override is None
    assert live_strategy.kappa == args.kappa
    assert live_strategy.sigma_override is None
