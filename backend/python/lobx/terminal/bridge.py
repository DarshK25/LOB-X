"""Bridge canonical pipeline state into the terminal TelemetryCollector.

The trading pipeline is the single source of truth: MarketState, the strategy
quote audit (``last_quote_audit``), and the ShadowQuoteEngine / PortfolioEngine.
These builders convert that state into terminal snapshots so the terminal renders
real runtime values — never recomputed metrics or fabricated data. Both the live
StrategyRunner and the backtest HistoricalReplay call the same functions, so the
terminal sees one telemetry contract regardless of event source.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lobx.terminal.telemetry import (
    ExecutionSnapshot,
    MarketSnapshot,
    OrderSnapshot,
    PortfolioSnapshot,
    StrategySnapshot,
    TelemetryCollector,
)

if TYPE_CHECKING:
    from lobx.execution.shadow_engine import ShadowOrder, ShadowQuoteEngine
    from lobx.strategy.base import MarketState, QuotePair


def market_snapshot(
    state: MarketState,
    *,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> MarketSnapshot:
    """Build a MarketSnapshot from the frozen MarketState the strategy saw."""
    return MarketSnapshot(
        timestamp=state.timestamp,
        mid=state.mid_price,
        best_bid=state.best_bid,
        best_ask=state.best_ask,
        spread=state.spread,
        microprice=state.microprice,
        volatility=state.volatility,
        imbalance=state.imbalance,
        last_trade_price=state.trade_price,
        last_trade_side=state.trade_side,
        last_trade_qty=state.trade_quantity,
        bids=bids,
        asks=asks,
    )


def strategy_snapshot(
    name: str,
    state: MarketState,
    quotes: QuotePair,
    audit: dict[str, Any],
) -> StrategySnapshot:
    """Build a StrategySnapshot from the strategy's own quote audit.

    Model inputs (gamma/kappa/sigma/tau/reservation price/spread) come straight
    from ``last_quote_audit`` so displayed values are exactly what produced the
    quotes. Baselines that do not populate the audit fall back to the state mid.
    """
    return StrategySnapshot(
        timestamp=state.timestamp,
        name=name,
        reservation_price=float(audit.get("reservation_price", state.mid_price)),
        optimal_spread=float(audit.get("model_full_spread", state.spread)),
        bid_price=quotes.bid.price if quotes.bid else None,
        ask_price=quotes.ask.price if quotes.ask else None,
        bid_qty=quotes.bid.qty if quotes.bid else None,
        ask_qty=quotes.ask.qty if quotes.ask else None,
        inventory=state.inventory,
        gamma=_opt_float(audit.get("gamma")),
        kappa=_opt_float(audit.get("effective_kappa", audit.get("kappa"))),
        sigma=_opt_float(audit.get("sigma")),
        tau=_opt_float(audit.get("time_remaining_seconds")),
        hawkes_intensity=_opt_float(audit.get("hawkes_intensity")),
        details=dict(audit),
    )


def _order_snapshot(order: ShadowOrder) -> OrderSnapshot:
    return OrderSnapshot(
        order_id=order.order_id,
        side=order.side,
        price=order.price,
        qty=order.qty,
        remaining_qty=order.remaining_qty,
        placed_at=order.placed_at,
        active_at=order.active_at,
        queue_ahead=order.queue_ahead_qty,
        status=order.status,
        filled_qty=order.qty - order.remaining_qty,
    )


def execution_snapshot(engine: ShadowQuoteEngine, timestamp: int) -> ExecutionSnapshot:
    """Build an ExecutionSnapshot from the engine's live orders and fills.

    ``fill_rate`` is fills per order placed (a defined, honest ratio), not a
    fabricated probability. ``avg_queue_ahead`` averages the displayed quantity
    ahead of the currently resting orders.
    """
    active = [
        _order_snapshot(o)
        for o in (engine.resting_bid, engine.resting_ask)
        if o is not None
    ]
    fills = engine.fills
    bid_fills = sum(1 for f in fills if f["side"] == "buy")
    ask_fills = sum(1 for f in fills if f["side"] == "sell")
    orders_placed = max(engine._next_order_id - 1, 0)
    fill_rate = len(fills) / orders_placed if orders_placed else 0.0
    avg_queue = (
        sum(o.queue_ahead for o in active) / len(active) if active else 0.0
    )
    return ExecutionSnapshot(
        timestamp=timestamp,
        active_orders=active,
        recent_fills=list(fills[-10:]),
        total_fills=len(fills),
        bid_fills=bid_fills,
        ask_fills=ask_fills,
        fill_rate=fill_rate,
        avg_queue_ahead=avg_queue,
    )


def portfolio_snapshot(
    engine: ShadowQuoteEngine, mark_price: float, timestamp: int
) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from the PortfolioEngine accounting authority.

    Every PnL figure comes from ``PortfolioEngine.snapshot`` so the terminal and
    the session/backtest report cannot diverge.
    """
    snap = engine.portfolio.snapshot(mark_price)
    inv = engine.inventory
    return PortfolioSnapshot(
        timestamp=timestamp,
        inventory=snap["inventory"],
        cash=snap["cash"],
        realized_pnl=snap["realized_pnl"],
        unrealized_pnl=snap["unrealized_pnl"],
        fees=snap["fees"],
        gross_pnl=snap["gross_pnl"],
        net_pnl=snap["net_pnl"],
        max_inventory=inv.max_inventory,
        avg_inventory=inv.average_inventory,
    )


def publish_tick(
    collector: TelemetryCollector,
    *,
    state: MarketState,
    strategy_name: str,
    quotes: QuotePair,
    audit: dict[str, Any],
    engine: ShadowQuoteEngine,
    mark_price: float,
    bids: list[tuple[float, float]] | None = None,
    asks: list[tuple[float, float]] | None = None,
) -> None:
    """Publish one full pipeline tick (market, strategy, execution, portfolio)."""
    collector.update_market(market_snapshot(state, bids=bids, asks=asks))
    collector.update_strategy(strategy_snapshot(strategy_name, state, quotes, audit))
    collector.update_execution(execution_snapshot(engine, state.timestamp))
    collector.update_portfolio(portfolio_snapshot(engine, mark_price, state.timestamp))
    collector.sync_events(engine.events)


def publish_execution(
    collector: TelemetryCollector,
    *,
    engine: ShadowQuoteEngine,
    mark_price: float,
    timestamp: int,
) -> None:
    """Publish execution + portfolio state after a fill (no new market tick)."""
    collector.update_execution(execution_snapshot(engine, timestamp))
    collector.update_portfolio(portfolio_snapshot(engine, mark_price, timestamp))
    collector.sync_events(engine.events)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)
