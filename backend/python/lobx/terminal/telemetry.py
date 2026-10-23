"""
Telemetry layer for LOB-X terminal.

Provides structured event streaming from execution engine to terminal UI
without coupling business logic to presentation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque


@dataclass
class MarketSnapshot:
    """Current market state.

    ``bids``/``asks`` hold top-of-book depth as (price, qty) levels, best first.
    They are None until real depth is supplied; the terminal shows no book
    rather than fabricating one.
    """
    timestamp: int
    mid: float
    best_bid: float
    best_ask: float
    spread: float
    microprice: float | None = None
    volatility: float | None = None
    ofi: float | None = None
    imbalance: float | None = None
    last_trade_price: float | None = None
    last_trade_side: str | None = None
    last_trade_qty: float | None = None
    bids: list[tuple[float, float]] | None = None
    asks: list[tuple[float, float]] | None = None


@dataclass
class StrategySnapshot:
    """Current strategy state."""
    timestamp: int
    name: str
    reservation_price: float
    optimal_spread: float
    bid_price: float | None
    ask_price: float | None
    bid_qty: float | None
    ask_qty: float | None
    inventory: float
    gamma: float | None = None
    kappa: float | None = None
    sigma: float | None = None
    tau: float | None = None
    hawkes_intensity: float | None = None
    variance_term: float | None = None
    kappa_term: float | None = None
    spread_total: float | None = None
    sigma_log: float | None = None
    sigma_usd: float | None = None
    time_remaining: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderSnapshot:
    """Active or recent order state."""
    order_id: int
    side: str
    price: float
    qty: float
    remaining_qty: float
    placed_at: int
    active_at: int
    queue_ahead: float
    status: str
    fills: int = 0
    filled_qty: float = 0.0


@dataclass
class ExecutionSnapshot:
    """Current execution state."""
    timestamp: int
    active_orders: list[OrderSnapshot]
    recent_fills: list[dict[str, Any]]
    total_fills: int
    bid_fills: int
    ask_fills: int
    fill_rate: float
    avg_queue_ahead: float


@dataclass
class PortfolioSnapshot:
    """Current portfolio state."""
    timestamp: int
    inventory: float
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    gross_pnl: float
    net_pnl: float
    max_inventory: float
    avg_inventory: float


@dataclass
class TelemetryEvent:
    """Generic telemetry event."""
    event_type: str
    timestamp: int
    data: dict[str, Any]


class TelemetryCollector:
    """
    Collects and buffers telemetry from execution engine.
    
    Terminal subscribes to this collector to receive updates without
    directly coupling to execution internals.
    """
    
    def __init__(self, max_events: int = 1000, max_series_points: int = 500):
        self.max_events = max_events
        self.max_series_points = max_series_points
        
        # State snapshots (latest)
        self.market: MarketSnapshot | None = None
        self.strategy: StrategySnapshot | None = None
        self.execution: ExecutionSnapshot | None = None
        self.portfolio: PortfolioSnapshot | None = None
        
        # Event streams
        self.events: Deque[TelemetryEvent] = deque(maxlen=max_events)
        
        # Time series
        self.price_history: Deque[tuple[int, float]] = deque(maxlen=max_series_points)
        self.microprice_history: Deque[tuple[int, float]] = deque(maxlen=max_series_points)
        self.inventory_history: Deque[tuple[int, float]] = deque(maxlen=max_series_points)
        self.pnl_history: Deque[tuple[int, float]] = deque(maxlen=max_series_points)
        
        # Callbacks for live updates
        self._callbacks: list[Callable[[TelemetryEvent], None]] = []

        # Highest ShadowQuoteEngine event_id already mirrored into `events`.
        self._last_event_id = 0
        
    def subscribe(self, callback: Callable[[TelemetryEvent], None]) -> None:
        """Subscribe to telemetry events."""
        self._callbacks.append(callback)
        
    def emit(self, event: TelemetryEvent) -> None:
        """Emit a telemetry event."""
        self.events.append(event)
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception:
                pass  # Don't let callback errors break telemetry
    
    def update_market(self, snapshot: MarketSnapshot) -> None:
        """Update market state."""
        self.market = snapshot
        self.price_history.append((snapshot.timestamp, snapshot.mid))
        if snapshot.microprice is not None:
            self.microprice_history.append((snapshot.timestamp, snapshot.microprice))
        self.emit(TelemetryEvent("MARKET_UPDATE", snapshot.timestamp, {
            "mid": snapshot.mid,
            "spread": snapshot.spread,
            "volatility": snapshot.volatility,
        }))
    
    def update_strategy(self, snapshot: StrategySnapshot) -> None:
        """Update strategy state."""
        self.strategy = snapshot
        self.emit(TelemetryEvent("STRATEGY_UPDATE", snapshot.timestamp, {
            "reservation_price": snapshot.reservation_price,
            "bid": snapshot.bid_price,
            "ask": snapshot.ask_price,
            "inventory": snapshot.inventory,
        }))
    
    def update_execution(self, snapshot: ExecutionSnapshot) -> None:
        """Update execution state."""
        self.execution = snapshot
        self.emit(TelemetryEvent("EXECUTION_UPDATE", snapshot.timestamp, {
            "fills": snapshot.total_fills,
            "bid_fills": snapshot.bid_fills,
            "ask_fills": snapshot.ask_fills,
        }))
    
    def update_portfolio(self, snapshot: PortfolioSnapshot) -> None:
        """Update portfolio state."""
        self.portfolio = snapshot
        self.inventory_history.append((snapshot.timestamp, snapshot.inventory))
        self.pnl_history.append((snapshot.timestamp, snapshot.net_pnl))
        self.emit(TelemetryEvent("PORTFOLIO_UPDATE", snapshot.timestamp, {
            "inventory": snapshot.inventory,
            "net_pnl": snapshot.net_pnl,
        }))
    
    def add_fill(self, fill: dict[str, Any]) -> None:
        """Record a fill event."""
        self.emit(TelemetryEvent("FILL", fill["timestamp"], fill))
    
    def add_order(self, order: dict[str, Any]) -> None:
        """Record an order creation event."""
        self.emit(TelemetryEvent("ORDER_CREATED", order["timestamp"], order))
    
    def add_trade(self, trade: dict[str, Any]) -> None:
        """Record a market trade event."""
        self.emit(TelemetryEvent("TRADE", trade["timestamp"], trade))

    def sync_events(self, engine_events: list[dict[str, Any]]) -> int:
        """Mirror new ShadowQuoteEngine ledger events into the event stream.

        The ShadowQuoteEngine ledger is the canonical event source; this copies
        events not yet seen (by their monotonic ``event_id``) so the terminal
        renders real transitions rather than a parallel event vocabulary.
        Returns the number of events ingested.
        """
        ingested = 0
        for event in engine_events:
            event_id = event.get("event_id", 0)
            if event_id <= self._last_event_id:
                continue
            self.events.append(
                TelemetryEvent(event["event_type"], event["timestamp"], event)
            )
            self._last_event_id = event_id
            ingested += 1
        return ingested
