"""Exchange fee schedules (fractions of notional; negative maker = rebate).

The Binance VIP0 rows are the public retail schedules. ``vip9`` and
``mm_rebate`` are scenarios for a high-volume or market-maker-program
participant; they show what a strategy would earn at that tier and must not
be reported as achievable by a retail account.
"""
from __future__ import annotations

FEE_PROFILES: dict[str, dict] = {
    "vip0": {"maker": 0.0002, "taker": 0.0005, "label": "Binance USD-M VIP0 (2.0 / 5.0 bp)"},
    "vip9": {"maker": 0.0, "taker": 0.00017, "label": "VIP9 scenario (0.0 / 1.7 bp)"},
    "mm_rebate": {"maker": -0.00005, "taker": 0.00017,
                  "label": "MM-program scenario (-0.5 / 1.7 bp)"},
    "spot_vip0": {"maker": 0.001, "taker": 0.001, "label": "Binance spot VIP0 (10 / 10 bp)"},
    "zero": {"maker": 0.0, "taker": 0.0, "label": "No fees (diagnostic only)"},
}
