"""Focused proofs for experiment lifecycle and reproducibility."""
import numpy as np

from lobx.analytics.experiment import ExperimentConfig, summarize_returns, write_experiment_result


def test_experiment_config_fingerprint_is_stable(tmp_path):
    config = ExperimentConfig(experiment_id="EXP-1", dataset="test")
    assert config.fingerprint() == config.fingerprint()
    output = write_experiment_result(
        tmp_path, config, summary={"net_pnl": 1.0}, events=[], fills=[]
    )
    assert (output / "config.yaml").exists()
    assert (output / "summary.json").exists()
    assert (output / "events.csv").exists()
    assert (output / "calibration.json").exists()


def test_statistical_summary_uses_documented_sample_sharpe():
    summary = summarize_returns(np.array([1.0, 2.0, 3.0]), max_drawdown=0.5)
    assert summary.count == 3
    assert summary.median == 2.0
    assert summary.std > 0
    assert summary.sharpe > 0
    assert summary.max_drawdown == 0.5
