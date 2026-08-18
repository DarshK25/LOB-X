"""
Latency benchmark.

Measures P50/P95/P99 latency of a single add_limit_order() call in microseconds.
"""
import time
import random
import statistics
import lobx_cpp


def run(n: int = 100_000, seed: int = 42) -> None:
    rng      = random.Random(seed)
    book     = lobx_cpp.OrderBook()
    latencies: list[float] = []

    for i in range(n):
        order = lobx_cpp.Order(
            i,
            10_000 + rng.randint(-10, 10),
            rng.randint(1, 10),
            rng.random() < 0.5,
        )
        t0 = time.perf_counter_ns()
        book.add_limit_order(order)
        t1 = time.perf_counter_ns()
        latencies.append((t1 - t0) / 1_000.0)  # ns → µs

    latencies.sort()
    p50  = statistics.median(latencies)
    p95  = latencies[int(0.95 * n)]
    p99  = latencies[int(0.99 * n)]
    mean = statistics.mean(latencies)

    print(f"[latency] n={n:,}  mean={mean:.2f}µs  p50={p50:.2f}µs  p95={p95:.2f}µs  p99={p99:.2f}µs")


if __name__ == "__main__":
    run()
