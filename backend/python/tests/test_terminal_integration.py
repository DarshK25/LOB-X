"""Integration tests for terminal system."""
from collections import deque

from lobx.terminal.telemetry import (
    MarketSnapshot,
    PortfolioSnapshot,
    StrategySnapshot,
    TelemetryCollector,
)
from lobx.terminal.app import LOBXTerminal


class TestTelemetryCollector:
    """Test telemetry collection."""
    
    def test_initialization(self):
        """Collector initializes empty."""
        collector = TelemetryCollector()
        
        assert collector.market is None
        assert collector.strategy is None
        assert collector.execution is None
        assert collector.portfolio is None
        assert len(collector.events) == 0
    
    def test_market_update(self):
        """Market snapshot updates time series."""
        collector = TelemetryCollector()
        
        snapshot = MarketSnapshot(
            timestamp=1000,
            mid=100.0,
            best_bid=99.9,
            best_ask=100.1,
            spread=0.2,
        )
        
        collector.update_market(snapshot)
        
        assert collector.market == snapshot
        assert len(collector.price_history) == 1
        assert collector.price_history[0] == (1000, 100.0)
    
    def test_portfolio_update(self):
        """Portfolio snapshot updates PnL history."""
        collector = TelemetryCollector()
        
        snapshot = PortfolioSnapshot(
            timestamp=1000,
            inventory=0.01,
            cash=100.0,
            realized_pnl=5.0,
            unrealized_pnl=2.0,
            fees=1.0,
            gross_pnl=7.0,
            net_pnl=6.0,
            max_inventory=0.02,
            avg_inventory=0.015,
        )
        
        collector.update_portfolio(snapshot)
        
        assert collector.portfolio == snapshot
        assert len(collector.pnl_history) == 1
        assert collector.pnl_history[0] == (1000, 6.0)
    
    def test_event_stream(self):
        """Events are buffered with max length."""
        collector = TelemetryCollector(max_events=5)
        
        for i in range(10):
            collector.add_fill({
                "timestamp": i,
                "side": "buy",
                "qty": 0.01,
                "price": 100.0,
            })
        
        # Only last 5 should be kept
        assert len(collector.events) == 5
        assert collector.events[0].timestamp == 5


class TestTerminalViews:
    """Test terminal view rendering."""
    
    def test_terminal_initialization(self):
        """Terminal initializes with telemetry."""
        collector = TelemetryCollector()
        terminal = LOBXTerminal(collector, symbol="BTCUSDT")
        
        assert terminal.symbol == "BTCUSDT"
        assert terminal.telemetry == collector
    
    def test_header_with_data(self):
        """Header shows current state."""
        collector = TelemetryCollector()
        terminal = LOBXTerminal(collector)
        
        # Add market and portfolio data
        collector.update_market(MarketSnapshot(
            timestamp=1000,
            mid=50000.0,
            best_bid=49999.0,
            best_ask=50001.0,
            spread=2.0,
        ))
        
        collector.update_portfolio(PortfolioSnapshot(
            timestamp=1000,
            inventory=0.1,
            cash=1000.0,
            realized_pnl=50.0,
            unrealized_pnl=25.0,
            fees=5.0,
            gross_pnl=75.0,
            net_pnl=70.0,
            max_inventory=0.15,
            avg_inventory=0.12,
        ))
        
        header = terminal.create_header()
        
        # Header should contain market and PnL info
        assert header is not None
    
    def test_view_switching(self):
        """Views can be switched."""
        from lobx.terminal.app import TerminalView
        
        collector = TelemetryCollector()
        terminal = LOBXTerminal(collector)
        
        assert terminal.current_view == TerminalView.OVERVIEW
        
        terminal.switch_view(TerminalView.MARKET)
        assert terminal.current_view == TerminalView.MARKET
        
        terminal.switch_view(TerminalView.PNL)
        assert terminal.current_view == TerminalView.PNL


class TestViewComposition:
    """Views that stack a stats table above a chart must compose cleanly.

    Regression: MarketView/PnLView built ``Text()`` and appended the stats
    ``Table`` into it, which raises ``TypeError: Only str or Text can be
    appended to Text``. They now use ``rich.console.Group``.
    """

    def _populated_collector(self) -> TelemetryCollector:
        collector = TelemetryCollector()
        # Two points so the chart renders a line rather than the empty state.
        for i in range(2):
            collector.update_market(MarketSnapshot(
                timestamp=1000 + i,
                mid=50000.0 + i,
                best_bid=49999.0,
                best_ask=50001.0,
                spread=2.0,
                microprice=50000.5,
                volatility=0.0004,
                imbalance=0.1,
            ))
            collector.update_portfolio(PortfolioSnapshot(
                timestamp=1000 + i,
                inventory=0.004,
                cash=10.0,
                realized_pnl=-1.0,
                unrealized_pnl=-1.15,
                fees=0.2,
                gross_pnl=-1.95,
                net_pnl=-2.15,
                max_inventory=0.005,
                avg_inventory=0.003,
            ))
        return collector

    def _renders(self, panel) -> str:
        from io import StringIO
        from rich.console import Console
        console = Console(file=StringIO(), width=80)
        console.print(panel)  # raises if the renderable tree is malformed
        return console.file.getvalue()

    def test_market_view_renders_table_and_chart(self):
        from lobx.terminal.views import MarketView

        panel = MarketView(self._populated_collector()).render()
        out = self._renders(panel)
        assert "Mid" in out and "50,00" in out  # stats table survived

    def test_pnl_view_renders_table_and_chart(self):
        from lobx.terminal.views import PnLView

        panel = PnLView(self._populated_collector()).render()
        out = self._renders(panel)
        assert "Net PnL" in out and "Fees" in out  # stats table survived


class TestChartRendering:
    """Test chart rendering."""
    
    def test_line_chart_with_data(self):
        """Line chart renders from time series."""
        from lobx.terminal.charts import TerminalChart
        
        chart = TerminalChart(height=10, width=50)
        
        data = deque([(i, 100.0 + i * 0.1) for i in range(100)])
        
        result = chart.render_line(data, title="Test", color="cyan")
        
        # Should produce non-empty text
        assert len(str(result)) > 0
    
    def test_chart_empty_data(self):
        """Chart handles empty data gracefully."""
        from lobx.terminal.charts import TerminalChart
        
        chart = TerminalChart(height=10, width=50)
        result = chart.render_line(deque(), title="Empty")
        
        assert "Waiting for data" in str(result)
    
    def test_sparkline(self):
        """Sparkline renders compact chart."""
        from lobx.terminal.charts import render_sparkline
        
        values = [1.0, 2.0, 3.0, 2.5, 2.0, 3.0, 4.0]
        result = render_sparkline(values, width=20)
        
        assert len(result) == 20
        assert all(c in " ▁▂▃▄▅▆▇█" for c in result)
