"""
Memory benchmark.

Estimates RSS growth as the order book grows to N resting orders.
Requires the `psutil` package (pip install psutil).
"""
import sys
import lobx_cpp

try:
    import psutil, os
    _proc = psutil.Process(os.getpid())
    def rss_mb() -> float:
        return _proc.memory_info().rss / 1024 / 1024
except ImportError:
    def rss_mb() -> float:  # type: ignore[misc]
        return 0.0


def run(n: int = 500_000) -> None:
    book = lobx_cpp.OrderBook()
    mb0  = rss_mb()

    for i in range(n):
        # All at distinct prices so nothing crosses (resting).
        price  = 1 + (i % 10_000)
        is_buy = (i % 2 == 0)
        order  = lobx_cpp.Order(i, price, 10, is_buy)
        book.add_limit_order(order)

    mb1 = rss_mb()
    print(f"[memory] {n:,} resting orders  Δ RSS={mb1-mb0:.1f} MB  "
          f"({(mb1-mb0)*1024/n:.2f} KB/order)")


if __name__ == "__main__":
    run()
