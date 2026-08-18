"""Generate reports only from a successful real-data calibration."""
from __future__ import annotations

import json
from pathlib import Path

from analyze_market import run_analysis


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
    payload = {
        "status": "valid_real_data_fit",
        "source": {"trades": result["trades_file"], "depth": result["depth_file"]},
        "sigma": result["sigma"],
        "kappa": {
            "value_per_tick": kappa["kappa"],
            "r_squared": kappa["r_squared"],
            "p_value": kappa["p_value"],
            "n_points": kappa["n_points"],
        },
        "gamma": {
            "value": result["gamma"],
            "is_calibrated": False,
            "derivation": "1 / (max_inventory * kappa)",
            "assumption_max_inventory_btc": result["max_inventory"],
        },
    }
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
