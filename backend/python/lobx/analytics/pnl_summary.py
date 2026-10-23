"""Small, fee-aware P&L summaries for replay and research fills.

The replay ledger stores fills as dictionaries, while a few research callers
use the compact ``(side, price, qty, maker)`` tuple form.  This module keeps
the accounting independent of either representation and makes the fee
assumptions visible in the returned values.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


_SENSITIVITY_BPS = (0, 1, 10)


def summarize_fills(
    fills: Iterable[Mapping[str, Any] | Sequence[Any]],
    final_mid: float,
    maker_bps: float,
    taker_bps: float,
) -> dict[str, Any]:
    """Return mark-to-market P&L and explicit fee diagnostics.

    Parameters
    ----------
    fills:
        Existing fill dictionaries must provide ``side``, ``price`` and
        ``qty``.  A dictionary is treated as a maker fill by default, except
        for the replay's ``order_id == "forced_flatten"`` records.  A compact
        tuple/list may be supplied as ``(side, price, qty, maker)`` where the
        fourth value is either a boolean or ``"maker"``/``"taker"``.
    final_mid:
        The price at which the remaining signed inventory is marked.
    maker_bps, taker_bps:
        Fee rates in basis points.  Fees are charged on each fill's traded
        notional, using the fill's maker/taker classification.

    ``gross_pnl`` is the cash-flow P&L of every fill plus the value of the
    resulting signed inventory at ``final_mid``.  This is equivalent to
    summing ``signed_qty * (final_mid - fill_price)`` and works for both open
    and fully flattened inventories.

    ``fee_sensitivity`` reports net P&L if one uniform fee rate were applied
    to every fill at 0, 1, and 10 bps.  The supplied maker/taker rates remain
    the rates used for ``maker_fees``, ``taker_fees``, ``fees``, and
    ``net_pnl``.
    """
    final_mid = _finite_number(final_mid, "final_mid")
    if final_mid < 0.0:
        raise ValueError("final_mid cannot be negative")
    maker_bps = _non_negative_number(maker_bps, "maker_bps")
    taker_bps = _non_negative_number(taker_bps, "taker_bps")

    gross_pnl = 0.0
    traded_notional = 0.0
    maker_notional = 0.0
    taker_notional = 0.0
    normalised_fills = 0

    for fill in fills:
        side, price, qty, is_maker = _normalise_fill(fill)
        signed_qty = qty if side == "buy" else -qty
        gross_pnl += signed_qty * (final_mid - price)
        notional = price * qty
        traded_notional += notional
        if is_maker:
            maker_notional += notional
        else:
            taker_notional += notional
        normalised_fills += 1

    maker_fees = maker_notional * maker_bps / 10_000.0
    taker_fees = taker_notional * taker_bps / 10_000.0
    fees = maker_fees + taker_fees
    net_pnl = gross_pnl - fees
    reconciliation_residual = net_pnl - (gross_pnl - fees)
    breakeven_fee_bps = (
        gross_pnl / traded_notional * 10_000.0 if traded_notional else 0.0
    )
    fee_sensitivity_bps = {
        bps: gross_pnl - traded_notional * bps / 10_000.0
        for bps in _SENSITIVITY_BPS
    }

    # Numeric keys keep the programmatic API direct (``result["fee_sensitivity"][1]``);
    # JSON encoders render them as the corresponding string object keys.
    fee_sensitivity = dict(fee_sensitivity_bps)
    return {
        "gross_pnl": gross_pnl,
        "maker_fees": maker_fees,
        "taker_fees": taker_fees,
        "fees": fees,
        "net_pnl": net_pnl,
        "traded_notional": traded_notional,
        "breakeven_fee_bps": breakeven_fee_bps,
        "fee_sensitivity": fee_sensitivity,
        "fee_sensitivity_bps": fee_sensitivity_bps,
        "fee_sensitivity_labels": {
            f"{bps}_bps": value for bps, value in fee_sensitivity_bps.items()
        },
        "reconciliation_residual": reconciliation_residual,
        # Keep the naming used by the existing waterfall easy to consume.
        "pnl_reconciliation_residual": reconciliation_residual,
        "pnl_reconciled": abs(reconciliation_residual) <= 1e-12,
        "maker_notional": maker_notional,
        "taker_notional": taker_notional,
        "fills_attributed": normalised_fills,
    }


def _normalise_fill(fill: Mapping[str, Any] | Sequence[Any]) -> tuple[str, float, float, bool]:
    """Convert supported fill records to ``(side, price, qty, is_maker)``."""
    if isinstance(fill, Mapping):
        side_value = fill.get("side", fill.get("quote_side"))
        price_value = fill.get("price", fill.get("quote_price"))
        qty_value = fill.get("qty", fill.get("fill_qty"))
        is_maker = _maker_classification(fill)
    elif isinstance(fill, Sequence) and not isinstance(fill, (str, bytes, bytearray)):
        if len(fill) < 4:
            raise ValueError(
                "tuple fill records must have shape (side, price, qty, maker)"
            )
        side_value, price_value, qty_value = fill[:3]
        is_maker = _parse_maker_value(fill[3])
    else:
        raise TypeError("fill must be a mapping or (side, price, qty, maker) sequence")

    if side_value is None or price_value is None or qty_value is None:
        raise ValueError("fill records must provide side, price, and qty")
    side = str(side_value).lower()
    if side == "bid":
        side = "buy"
    elif side == "ask":
        side = "sell"
    if side not in {"buy", "sell"}:
        raise ValueError(f"fill side must be 'buy' or 'sell', got {side_value!r}")
    price = _finite_number(price_value, "fill price")
    qty = _finite_number(qty_value, "fill qty")
    if price < 0.0:
        raise ValueError("fill price cannot be negative")
    if qty <= 0.0:
        raise ValueError("fill qty must be positive")
    return side, price, qty, is_maker


def _maker_classification(fill: Mapping[str, Any]) -> bool:
    """Read maker/taker metadata while supporting the replay's old records."""
    for key in ("is_maker", "maker", "liquidity", "fee_type", "role", "execution_type"):
        if key in fill and fill[key] is not None:
            return _parse_maker_value(fill[key])
    if "is_taker" in fill and fill["is_taker"] is not None:
        return not _parse_maker_value(fill["is_taker"])
    # HistoricalReplay's only taker record is its explicit end-of-run
    # liquidation. All ordinary shadow fills are passive maker fills.
    return str(fill.get("order_id", "")).lower() != "forced_flatten"


def _parse_maker_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value in (0, 1):
            return bool(value)
    normalised = str(value).strip().lower()
    if normalised in {"maker", "m", "passive", "post_only", "post-only", "true", "1"}:
        return True
    if normalised in {
        "taker", "t", "aggressive", "market", "forced_flatten", "forced-flatten", "false", "0",
    }:
        return False
    raise ValueError(f"maker/taker classification must be maker or taker, got {value!r}")


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _non_negative_number(value: Any, name: str) -> float:
    number = _finite_number(value, name)
    if number < 0.0:
        raise ValueError(f"{name} cannot be negative")
    return number


__all__ = ["summarize_fills"]
