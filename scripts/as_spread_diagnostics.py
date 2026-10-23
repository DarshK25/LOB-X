"""Print AS spread decomposition for explicit horizon values.

This is a small evidence CLI for the canonical model.  It uses the same
``spread_decomposition`` helper as strategy diagnostics, so the variance and
liquidity components cannot drift from the quoted model.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.models.avellaneda.optimal_spread import spread_decomposition


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print canonical AS variance/kappa spread decomposition"
    )
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--kappa", type=float, default=1.5)
    parser.add_argument("--sigma-usd", type=float, default=0.31,
                        help="AS sigma in USD per sqrt-second")
    parser.add_argument("--taus", type=float, nargs="+", default=[1.0, 10.0, 60.0, 300.0])
    args = parser.parse_args()

    print(
        f"gamma={args.gamma:g} kappa={args.kappa:g} "
        f"sigma_usd={args.sigma_usd:g}"
    )
    print("tau_seconds variance_term_usd kappa_term_usd spread_total_usd")
    for tau in args.taus:
        terms = spread_decomposition(args.gamma, args.sigma_usd, tau, args.kappa)
        print(
            f"{tau:g} {terms['variance_term']:.12g} "
            f"{terms['kappa_term']:.12g} {terms['spread_total']:.12g}"
        )


if __name__ == "__main__":
    main()
