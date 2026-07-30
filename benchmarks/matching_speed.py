"""
Matching speed benchmark.

Measures single-threaded order submission throughput:
    - How many add_limit_order() calls/second can the C++ engine handle?
"""
import time
import random
import lobx_cpp


def run(n: int = 1_000_000, seed: int = 42) -> None:
    rng  = random.Random(seed)
    book = lobx_cpp.OrderBook()

    orders = [
        lobx_cpp.Order(i, 10_000 + rng.randint(-50, 50), rng.randint(1, 20), rng.random() < 0.5)
        for i in range(n)
    ]

    start = time.perf_counter()
    for o in orders:
        book.add_limit_order(o)
    elapsed = time.perf_counter() - start

    print(f"[matching_speed] {n:,} orders in {elapsed:.3f}s → {n/elapsed:,.0f} orders/s")


if __name__ == "__main__":
    run()
