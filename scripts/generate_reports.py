"""Generate reports only from a successful real-data calibration."""
from __future__ import annotations

import json
from pathlib import Path

from analyze_market import run_analysis
from lobx.analytics.calibration import write_calibration_artifact


def generate_reports() -> None:
    # ``run_analysis`` owns the sole calibration implementation.  Do not add
    # a diagnostics-only fallback or simulated spreads here.
    try:
        result = run_analysis()
    except ValueError as exc:
        print(f"REPORT NOT GENERATED: {exc}")
        raise SystemExit(1)
    reports_dir = Path("reports")
    reports_dir.mkdir(exist_ok=True)
    kappa = result["kappa"]
    artifact = write_calibration_artifact(
        reports_dir / "calibration.json",
        sigma=float(result["sigma"]["sigma_per_sqrt_second"]),
        kappa=float(kappa["kappa"]),
        source_files={
            **{f"trades_{index}": path for index, path in enumerate(result["trades_files"])},
            **{f"depth_{index}": path for index, path in enumerate(result["depth_files"])},
        },
        bucket_count=int(kappa["n_points"]),
        r_squared=float(kappa["r_squared"]),
        p_value=float(kappa["p_value"]),
        valid=None,
        usable=None,
        metadata={
            "gamma_assumption": result["gamma"],
            "max_inventory_btc": result["max_inventory"],
            "fit_points": kappa["n_points"],
        },
    )
    # Preserve the report-oriented fields while keeping the file a valid v1
    # artifact for explicit loading by the backtest/live runners.
    payload = artifact.to_dict()
    payload.update({
        "status": "valid_real_data_fit",
        "gamma": {
            "value": result["gamma"],
            "is_calibrated": False,
            "derivation": "1 / (max_inventory * kappa)",
            "assumption_max_inventory_btc": result["max_inventory"],
        },
    })
    (reports_dir / "calibration.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (reports_dir / "market_report.html").write_text(
        "<html><body><h1>LOB-X Market Calibration</h1>"
        "<p>See calibration.json and fill_curve_real_data.csv for the real-data evidence.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    print(f"Reports generated in {reports_dir.resolve()}")


if __name__ == "__main__":
    generate_reports()
