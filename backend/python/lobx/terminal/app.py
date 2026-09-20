"""
LOB-X Research Terminal Application.

Persistent terminal UI for quantitative trading research and backtesting.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import TYPE_CHECKING

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from lobx.terminal.views import (
    EventStreamView,
    ExecutionView,
    L2BookView,
    MarketView,
    PnLView,
    StrategyView,
)

if TYPE_CHECKING:
    from lobx.terminal.telemetry import TelemetryCollector


class TerminalView(Enum):
    """Available terminal views."""
    OVERVIEW = "overview"
    MARKET = "market"
    STRATEGY = "strategy"
    EXECUTION = "execution"
    PNL = "pnl"
    RESEARCH = "research"


class LOBXTerminal:
    """
    Persistent quantitative trading research terminal.
    
    Displays real-time market data, strategy state, execution, and PnL
    in a continuously updating terminal interface.
    """
    
    def __init__(
        self,
        telemetry: TelemetryCollector,
        symbol: str = "BTCUSDT",
        refresh_rate: float = 0.5,
    ):
        self.telemetry = telemetry
        self.symbol = symbol
        self.refresh_rate = refresh_rate
        self.console = Console()
        self.current_view = TerminalView.OVERVIEW
        
        # View components
        self.market_view = MarketView(telemetry)
        self.l2_view = L2BookView(telemetry)
        self.strategy_view = StrategyView(telemetry)
        self.execution_view = ExecutionView(telemetry)
        self.pnl_view = PnLView(telemetry)
        self.events_view = EventStreamView(telemetry, max_lines=8)
        
        self.start_time = time.time()
        self.last_update = 0
    
    def create_header(self) -> Panel:
        """Create terminal header."""
        if self.telemetry.market and self.telemetry.portfolio:
            mid = self.telemetry.market.mid
            pnl = self.telemetry.portfolio.net_pnl
            inv = self.telemetry.portfolio.inventory
            pnl_color = "green" if pnl >= 0 else "red"
            
            text = Text()
            text.append("LOB-X  ", style="bold magenta")
            text.append(f"{self.symbol}  ", style="bold white")
            text.append(f"Mid: ${mid:,.2f}  ", style="cyan")
            text.append(f"PnL: ", style="dim")
            text.append(f"${pnl:+,.2f}  ", style=f"bold {pnl_color}")
            text.append(f"Inv: {inv:+.4f} BTC", style="white")
        else:
            text = Text()
            text.append("LOB-X Research Terminal  ", style="bold magenta")
            text.append(f"{self.symbol}", style="white")
        
        return Panel(text, style="bold", border_style="magenta")
    
    def create_footer(self) -> Panel:
        """Create terminal footer."""
        runtime = int(time.time() - self.start_time)
        
        text = Text()
        text.append(f"View: {self.current_view.value}  ", style="bold")
        text.append(f"Runtime: {runtime}s  ", style="dim")
        
        if self.telemetry.strategy:
            text.append(f"Strategy: {self.telemetry.strategy.name}  ", style="cyan")
        
        if self.telemetry.execution:
            text.append(f"Fills: {self.telemetry.execution.total_fills}  ", style="white")
        
        text.append("Press 'q' to quit", style="dim yellow")
        
        return Panel(text, style="dim", border_style="dim")
    
    def create_overview_layout(self) -> Layout:
        """Create overview layout with all panels."""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        
        layout["body"].split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=1),
        )
        
        layout["left"].split_column(
            Layout(name="market", ratio=2),
            Layout(name="execution", ratio=1),
            Layout(name="events", ratio=1),
        )
        
        layout["right"].split_column(
            Layout(name="strategy", ratio=1),
            Layout(name="pnl", ratio=2),
        )
        
        return layout
    
    def create_market_layout(self) -> Layout:
        """Create market-focused layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        
        layout["body"].split_row(
            Layout(name="chart", ratio=2),
            Layout(name="side", ratio=1),
        )
        
        layout["side"].split_column(
            Layout(name="book", ratio=1),
            Layout(name="events", ratio=1),
        )
        
        return layout
    
    def create_strategy_layout(self) -> Layout:
        """Create strategy-focused layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        
        layout["body"].split_row(
            Layout(name="strategy", ratio=1),
            Layout(name="execution", ratio=1),
        )
        
        return layout
    
    def create_pnl_layout(self) -> Layout:
        """Create PnL-focused layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        
        layout["body"].split_row(
            Layout(name="pnl", ratio=2),
            Layout(name="side", ratio=1),
        )
        
        layout["side"].split_column(
            Layout(name="execution", ratio=1),
            Layout(name="events", ratio=1),
        )
        
        return layout
    
    def create_research_layout(self) -> Layout:
        """Create research summary layout."""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=3),
        )
        
        layout["body"].split_column(
            Layout(name="summary", ratio=1),
            Layout(name="charts", ratio=2),
        )
        
        layout["charts"].split_row(
            Layout(name="chart1", ratio=1),
            Layout(name="chart2", ratio=1),
        )
        
        return layout
    
    def get_current_layout(self) -> Layout:
        """Get layout for current view."""
        if self.current_view == TerminalView.OVERVIEW:
            return self.create_overview_layout()
        elif self.current_view == TerminalView.MARKET:
            return self.create_market_layout()
        elif self.current_view == TerminalView.STRATEGY:
            return self.create_strategy_layout()
        elif self.current_view == TerminalView.PNL:
            return self.create_pnl_layout()
        elif self.current_view == TerminalView.RESEARCH:
            return self.create_research_layout()
        else:
            return self.create_overview_layout()
    
    def update(self, layout: Layout) -> None:
        """Update layout with current data."""
        # Update common elements
        layout["header"].update(self.create_header())
        layout["footer"].update(self.create_footer())
        
        # Update view-specific content
        if self.current_view == TerminalView.OVERVIEW:
            layout["market"].update(self.market_view.render())
            layout["strategy"].update(self.strategy_view.render())
            layout["execution"].update(self.execution_view.render())
            layout["pnl"].update(self.pnl_view.render())
            layout["events"].update(self.events_view.render())
        
        elif self.current_view == TerminalView.MARKET:
            layout["chart"].update(self.market_view.render())
            # Real top-of-book depth from telemetry; empty until depth arrives.
            market = self.telemetry.market
            bids = market.bids if market and market.bids else []
            asks = market.asks if market and market.asks else []
            layout["book"].update(self.l2_view.render(bids, asks))
            layout["events"].update(self.events_view.render())
        
        elif self.current_view == TerminalView.STRATEGY:
            layout["strategy"].update(self.strategy_view.render())
            layout["execution"].update(self.execution_view.render())
        
        elif self.current_view == TerminalView.PNL:
            layout["pnl"].update(self.pnl_view.render())
            layout["execution"].update(self.execution_view.render())
            layout["events"].update(self.events_view.render())
        
        elif self.current_view == TerminalView.RESEARCH:
            # Summary panel
            summary = self._create_research_summary()
            layout["summary"].update(summary)
            layout["chart1"].update(self.market_view.render())
            layout["chart2"].update(self.pnl_view.render())
    
    def _create_research_summary(self) -> Panel:
        """Create research summary panel."""
        text = Text()
        text.append("Research Summary\n\n", style="bold magenta")
        
        if self.telemetry.portfolio and self.telemetry.execution:
            p = self.telemetry.portfolio
            e = self.telemetry.execution
            
            text.append(f"Net PnL: ", style="dim")
            pnl_color = "green" if p.net_pnl >= 0 else "red"
            text.append(f"${p.net_pnl:+,.2f}\n", style=f"bold {pnl_color}")
            
            text.append(f"Total Fills: {e.total_fills}\n", style="white")
            text.append(f"  Buy fills: {e.bid_fills}\n", style="green")
            text.append(f"  Sell fills: {e.ask_fills}\n", style="red")
            
            if e.total_fills > 0:
                buy_pct = e.bid_fills / e.total_fills * 100
                text.append(f"  Buy ratio: {buy_pct:.1f}%\n", style="dim")
            
            text.append(f"\nFees paid: ${p.fees:,.2f}\n", style="red")
            text.append(f"Max inventory: {p.max_inventory:.6f} BTC\n", style="white")
            text.append(f"Avg inventory: {p.avg_inventory:.6f} BTC\n", style="dim")
        
        return Panel(text, title="Summary", border_style="magenta")
    
    def run(self, duration: float | None = None) -> None:
        """
        Run terminal with live updates.
        
        Args:
            duration: Run for this many seconds (None = run forever)
        """
        layout = self.get_current_layout()
        
        start = time.time()
        
        with Live(layout, console=self.console, refresh_per_second=1/self.refresh_rate, screen=False) as live:
            try:
                while True:
                    self.update(layout)
                    live.refresh()
                    
                    if duration and (time.time() - start) >= duration:
                        break
                    
                    time.sleep(self.refresh_rate)
            
            except KeyboardInterrupt:
                pass
    
    def switch_view(self, view: TerminalView) -> None:
        """Switch to a different view."""
        self.current_view = view
    
    def run_with_backtest(self, backtest_result) -> None:
        """
        Run terminal showing backtest replay.
        
        Simulates real-time updates from backtest data.
        """
        # TODO: Implement backtest replay animation
        # This would step through events and update telemetry in real-time
        pass


def create_terminal(telemetry: TelemetryCollector, symbol: str = "BTCUSDT") -> LOBXTerminal:
    """Factory function to create terminal instance."""
    return LOBXTerminal(telemetry, symbol)
