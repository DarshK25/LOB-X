"""Terminal view components for LOB-X."""
from __future__ import annotations

from typing import TYPE_CHECKING

from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lobx.terminal.charts import TerminalChart, render_sparkline

if TYPE_CHECKING:
    from lobx.terminal.telemetry import TelemetryCollector


class MarketView:
    """Market data visualization."""
    
    def __init__(self, telemetry: TelemetryCollector):
        self.telemetry = telemetry
        self.chart = TerminalChart(height=8, width=50)
    
    def render(self) -> Panel:
        """Render market view."""
        if not self.telemetry.market:
            return Panel("Waiting for market data...", title="Market", border_style="cyan")
        
        m = self.telemetry.market
        
        # Market stats table
        stats = Table.grid(padding=(0, 2))
        stats.add_column(justify="right", style="dim")
        stats.add_column(justify="left", style="bold cyan")
        
        stats.add_row("Mid", f"${m.mid:,.2f}")
        stats.add_row("Spread", f"${m.spread:.2f}")
        stats.add_row("Best Bid", f"${m.best_bid:,.2f}")
        stats.add_row("Best Ask", f"${m.best_ask:,.2f}")
        
        if m.microprice:
            stats.add_row("Microprice", f"${m.microprice:,.2f}")
        if m.volatility:
            stats.add_row("Volatility", f"{m.volatility:.4f}")
        if m.imbalance is not None:
            stats.add_row("Imbalance", f"{m.imbalance:+.3f}")
        
        # Price chart
        chart_text = self.chart.render_line(
            self.telemetry.price_history,
            title="",
            color="cyan",
            show_value=False
        )
        
        # Combine
        content = Text()
        content.append(stats)
        content.append("\n")
        content.append(chart_text)
        
        return Panel(content, title="Market", border_style="cyan")


class L2BookView:
    """L2 order book ladder."""
    
    def __init__(self, telemetry: TelemetryCollector):
        self.telemetry = telemetry
        self.depth = 5
    
    def render(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> Panel:
        """Render L2 book ladder."""
        table = Table(show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("Bid Qty", justify="right", style="green")
        table.add_column("Bid Price", justify="right", style="bold green")
        table.add_column(" ", justify="center", width=3)
        table.add_column("Ask Price", justify="left", style="bold red")
        table.add_column("Ask Qty", justify="left", style="red")
        
        # Show top N levels
        asks_display = list(reversed(asks[:self.depth]))
        bids_display = bids[:self.depth]
        
        # Asks (reversed)
        for price, qty in asks_display:
            bar = "█" * min(int(qty * 10), 20)
            table.add_row("", "", "", f"${price:,.2f}", f"{qty:.3f} {bar}")
        
        # Spread row
        if bids and asks:
            spread = asks[0][0] - bids[0][0]
            table.add_row("", "", f"━━━", "", "", style="dim")
        
        # Bids
        for price, qty in bids_display:
            bar = "█" * min(int(qty * 10), 20)
            table.add_row(f"{bar} {qty:.3f}", f"${price:,.2f}", "", "", "")
        
        return Panel(table, title="L2 Book", border_style="yellow")


class StrategyView:
    """Strategy state panel."""
    
    def __init__(self, telemetry: TelemetryCollector):
        self.telemetry = telemetry
    
    def render(self) -> Panel:
        """Render strategy state."""
        if not self.telemetry.strategy:
            return Panel("Waiting for strategy...", title="Strategy", border_style="magenta")
        
        s = self.telemetry.strategy
        
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", style="dim")
        table.add_column(justify="left")
        
        table.add_row("Strategy", s.name, style="bold magenta")
        table.add_row("", "")
        table.add_row("Reservation", f"${s.reservation_price:,.2f}", style="bold cyan")
        table.add_row("Spread", f"${s.optimal_spread:.2f}", style="cyan")
        
        if s.bid_price and s.ask_price:
            table.add_row("", "")
            table.add_row("Bid Quote", f"${s.bid_price:,.2f} × {s.bid_qty:.4f}", style="bold green")
            table.add_row("Ask Quote", f"${s.ask_price:,.2f} × {s.ask_qty:.4f}", style="bold red")
        
        table.add_row("", "")
        table.add_row("Inventory", f"{s.inventory:+.6f} BTC", style="bold white")
        
        if s.gamma:
            table.add_row("γ (risk aversion)", f"{s.gamma:.4f}", style="dim")
        if s.kappa:
            table.add_row("κ (order flow)", f"{s.kappa:.4f}", style="dim")
        if s.sigma:
            table.add_row("σ (volatility)", f"{s.sigma:.6f}", style="dim")
        if s.hawkes_intensity:
            table.add_row("λ (Hawkes)", f"{s.hawkes_intensity:.4f}", style="dim yellow")
        
        return Panel(table, title="Strategy", border_style="magenta")


class ExecutionView:
    """Execution and order state."""
    
    def __init__(self, telemetry: TelemetryCollector):
        self.telemetry = telemetry
    
    def render(self) -> Panel:
        """Render execution state."""
        if not self.telemetry.execution:
            return Panel("Waiting for execution...", title="Execution", border_style="yellow")
        
        e = self.telemetry.execution
        
        # Summary stats
        stats = Text()
        stats.append(f"Fills: {e.total_fills}  ", style="bold white")
        stats.append(f"Buys: {e.bid_fills}  ", style="green")
        stats.append(f"Sells: {e.ask_fills}\n", style="red")
        stats.append(f"Fill Rate: {e.fill_rate:.1%}  ", style="cyan")
        stats.append(f"Avg Queue: {e.avg_queue_ahead:.3f}\n", style="dim")
        
        # Active orders
        if e.active_orders:
            stats.append("\nActive Orders:\n", style="bold")
            for order in e.active_orders[-3:]:
                color = "green" if order.side == "bid" else "red"
                stats.append(
                    f"  {order.side.upper()} ${order.price:,.2f} "
                    f"[queue: {order.queue_ahead:.3f}] {order.status}\n",
                    style=color
                )
        
        # Recent fills
        if e.recent_fills:
            stats.append("\nRecent Fills:\n", style="bold")
            for fill in e.recent_fills[-3:]:
                color = "green" if fill["side"] == "buy" else "red"
                stats.append(
                    f"  {fill['side'].upper()} {fill['qty']:.4f} @ ${fill['price']:,.2f}\n",
                    style=color
                )
        
        return Panel(stats, title="Execution", border_style="yellow")


class PnLView:
    """PnL and inventory tracking."""
    
    def __init__(self, telemetry: TelemetryCollector):
        self.telemetry = telemetry
        self.chart = TerminalChart(height=8, width=50)
    
    def render(self) -> Panel:
        """Render PnL state."""
        if not self.telemetry.portfolio:
            return Panel("Waiting for portfolio...", title="PnL", border_style="green")
        
        p = self.telemetry.portfolio
        
        # PnL stats
        stats = Table.grid(padding=(0, 2))
        stats.add_column(justify="right", style="dim")
        stats.add_column(justify="left")
        
        pnl_color = "green" if p.net_pnl >= 0 else "red"
        stats.add_row("Net PnL", f"${p.net_pnl:+,.2f}", style=f"bold {pnl_color}")
        stats.add_row("Gross PnL", f"${p.gross_pnl:+,.2f}", style="cyan")
        stats.add_row("Realized", f"${p.realized_pnl:+,.2f}", style="white")
        stats.add_row("Unrealized", f"${p.unrealized_pnl:+,.2f}", style="dim")
        stats.add_row("Fees", f"${p.fees:,.2f}", style="red")
        stats.add_row("", "")
        stats.add_row("Inventory", f"{p.inventory:+.6f} BTC", style="bold white")
        stats.add_row("Cash", f"${p.cash:+,.2f}", style="cyan")
        
        # PnL chart
        chart_text = self.chart.render_line(
            self.telemetry.pnl_history,
            title="",
            color="green" if p.net_pnl >= 0 else "red",
            show_value=False
        )
        
        content = Text()
        content.append(stats)
        content.append("\n")
        content.append(chart_text)
        
        return Panel(content, title="PnL", border_style=pnl_color)


class EventStreamView:
    """Scrolling event feed."""
    
    def __init__(self, telemetry: TelemetryCollector, max_lines: int = 10):
        self.telemetry = telemetry
        self.max_lines = max_lines
    
    def render(self) -> Panel:
        """Render event stream."""
        events = list(self.telemetry.events)[-self.max_lines:]
        
        if not events:
            return Panel("Waiting for events...", title="Event Stream", border_style="dim")
        
        text = Text()
        for event in events:
            ts_sec = event.timestamp / 1000.0
            time_str = f"{ts_sec:.3f}"
            
            event_type = event.event_type
            color = "dim"
            
            if event_type == "FILL":
                color = "green" if event.data.get("side") == "buy" else "red"
                text.append(f"{time_str}  ", style="dim")
                text.append(f"FILL  ", style=f"bold {color}")
                text.append(f"{event.data.get('side', '?').upper()} ", style=color)
                text.append(f"{event.data.get('qty', 0):.4f} @ ", style=color)
                text.append(f"${event.data.get('price', 0):,.2f}\n", style=color)
            
            elif event_type == "ORDER_CREATED":
                side = event.data.get("side", "?")
                color = "green" if side == "bid" else "red"
                text.append(f"{time_str}  ", style="dim")
                text.append(f"ORDER ", style="bold yellow")
                text.append(f"{side.upper()} @ ${event.data.get('price', 0):,.2f}\n", style=color)
            
            elif event_type == "TRADE":
                text.append(f"{time_str}  ", style="dim")
                text.append(f"TRADE ", style="dim cyan")
                aggr = "SELL" if event.data.get("is_buyer_maker") else "BUY"
                text.append(f"{aggr} ${event.data.get('price', 0):,.2f}\n", style="dim")
            
            else:
                text.append(f"{time_str}  {event_type}\n", style="dim")
        
        return Panel(text, title="Event Stream", border_style="dim white")
