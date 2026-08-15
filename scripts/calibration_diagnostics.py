"""Print the same real-data calibration used by the production pipeline.

This is deliberately a thin entry point.  It does not contain its own arrays
or alternate fit logic, preventing a diagnostics-only placeholder result from
being reported as production evidence.
"""
from pathlib import Path
from analyze_market import run_analysis


def main() -> None:
    try:
        result = run_analysis([Path("data/raw"), Path("data/fresh_calibration"), Path("data/raw_watch")])
    except (ValueError, SystemExit) as exc:
        print(f"\nVERDICT: NOT CALIBRATED — {exc}")
        raise SystemExit(1)
    kappa = result["kappa"]["kappa"]
    gamma = result["gamma"]
    print("\n=== Literature Comparison ===")
    print(
        "Avellaneda & Stoikov (2008) specify an exponential arrival-intensity "
        "form A exp(-kappa*delta), but do not provide a universal numerical "
        "kappa or gamma range: their values depend on the asset, quote-distance "
        "unit, and inventory horizon."
    )
    print(
        f"This run estimates kappa={kappa:.8f} per tick from BTCUSDT quote exposure; "
        f"gamma={gamma:.8f} is explicitly derived from the 10 BTC inventory assumption, "
        "so neither is presented as directly validated by the A-S paper."
    )
    print("Reference: Avellaneda & Stoikov, Quantitative Finance 8(3), 2008, doi:10.1080/14697680701381228.")


if __name__ == "__main__":
    main()
