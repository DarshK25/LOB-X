#!/usr/bin/env python3
"""
Demo: LOB-X Research Terminal

Demonstrates the persistent terminal with simulated backtest data.
"""
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from lobx.terminal.telemetry import (
    ExecutionSnapshot,
    MarketSnapshot,
    OrderSnapshot,
    PortfolioSnapshot,
    StrategySnapshot,
    TelemetryCollector,
)
from lobx.terminal.app import LOBXTerminal


def simulate_market_data(collector: TelemetryCollector, duration: int = 30):
    """Simulate market data updates."""
    import math
    
    base_price = 50000.0
    start_time = int(time.time() * 1000)
    
    for i in range(duration * 10):  # 10 updates per second
        timestamp = start_time + i * 100
        t = i / 10.0
        
        # Simulate price oscillation
        mid = base_price + 10 * math.sin(t / 3.0)
        spread = 0.5 + 0.2 * abs(math.sin(t / 5.0))
        volatility = 0.0001 + 0.00005 * abs(math.cos(t / 7.0))
        
        collector.update_market(MarketSnapshot(
            timestamp=timestamp,
            mid=mid,
            best_bid=mid - spread/2,
            best_ask=mid + spread/2,
            spread=spread,
            microprice=mid + 0.05 * math.sin(t),
            volatility=volatility,
            imbalance=0.1 * math.cos(t / 4.0),
        ))
        
        # Simulate strategy state
        inventory = 0.01 * math.sin(t / 8.0)
        reservation = mid + inventory * 100
        
        collector.update_strategy(StrategySnapshot(
            timestamp=timestamp,
            name="Avellaneda-Stoikov",
            reservation_price=reservation,
            optimal_spread=spread,
            bid_price=reservation - spread/2,
            ask_price=reservation + spread/2,
            bid_qty=0.001,
            ask_qty=0.001,
            inventory=inventory,
            gamma=0.1,
            kappa=1.5,
            sigma=volatility,
            hawkes_intensity=1.0 + 0.3 * abs(math.sin(t / 6.0)),
        ))
        
        # Simulate fills occasionally
        if i % 50 == 0:
            side = "buy" if math.sin(t) > 0 else "sell"
            collector.add_fill({
                "timestamp": timestamp,
                "side": side,
                "price": mid + (1 if side == "sell" else -1),
                "qty": 0.001,
                "order_id": i // 50,
            })
        
        # Simulate execution state
        fills_count = i // 50
        bid_fills = sum(1 for j in range(fills_count) if math.sin(j) < 0)
        ask_fills = fills_count - bid_fills
        
        collector.update_execution(ExecutionSnapshot(
            timestamp=timestamp,
            active_orders=[
                OrderSnapshot(
                    order_id=1000,
                    side="bid",
                    price=mid - spread/2,
                    qty=0.001,
                    remaining_qty=0.001,
                    placed_at=timestamp - 1000,
                    active_at=timestamp - 900,
                    queue_ahead=0.5 * abs(math.sin(t)),
                    status="ACTIVE",
                ),
                OrderSnapshot(
                    order_id=1001,
                    side="ask",
                    price=mid + spread/2,
                    qty=0.001,
                    remaining_qty=0.001,
                    placed_at=timestamp - 1000,
                    active_at=timestamp - 900,
                    queue_ahead=0.3 * abs(math.cos(t)),
                    status="ACTIVE",
                ),
            ],
            recent_fills=[],
            total_fills=fills_count,
            bid_fills=bid_fills,
            ask_fills=ask_fills,
            fill_rate=0.15 if fills_count > 0 else 0.0,
            avg_queue_ahead=0.4,
        ))
        
        # Simulate portfolio
        pnl = 10.0 * math.sin(t / 10.0) + 5.0
        
        collector.update_portfolio(PortfolioSnapshot(
            timestamp=timestamp,
            inventory=inventory,
            cash=1000.0 + pnl,
            realized_pnl=pnl * 0.7,
            unrealized_pnl=pnl * 0.3,
            fees=fills_count * 0.05,
            gross_pnl=pnl + fills_count * 0.05,
            net_pnl=pnl,
            max_inventory=0.02,
            avg_inventory=abs(inventory * 0.8),
        ))
        
        time.sleep(0.1)


def main():
    """Run terminal demo."""
    print("LOB-X Research Terminal Demo")
    print("=" * 60)
    print()
    print("This demo simulates a live backtest with the persistent terminal.")
    print("Press Ctrl+C to exit.")
    print()
    
    # Create telemetry collector
    collector = TelemetryCollector(max_events=100, max_series_points=300)
    
    # Create terminal
    terminal = LOBXTerminal(collector, symbol="BTCUSDT", refresh_rate=0.2)
    
    # Start data simulation in background
    import threading
    sim_thread = threading.Thread(target=simulate_market_data, args=(collector, 300))
    sim_thread.daemon = True
    sim_thread.start()
    
    # Run terminal
    try:
        terminal.run(duration=300)  # Run for 5 minutes or until Ctrl+C
    except KeyboardInterrupt:
        print("\n\nTerminal stopped.")
    
    # Print final summary
    if collector.portfolio:
        print("\n" + "=" * 60)
        print("FINAL SUMMARY")
        print("=" * 60)
        p = collector.portfolio
        e = collector.execution
        
        print(f"Net PnL:      ${p.net_pnl:+,.2f}")
        print(f"Gross PnL:    ${p.gross_pnl:+,.2f}")
        print(f"Fees:         ${p.fees:,.2f}")
        print(f"Fills:        {e.total_fills}")
        print(f"  Buy fills:  {e.bid_fills}")
        print(f"  Sell fills: {e.ask_fills}")
        print(f"Max Inventory: {p.max_inventory:.6f} BTC")
        print(f"Avg Inventory: {p.avg_inventory:.6f} BTC")


if __name__ == "__main__":
    main()
