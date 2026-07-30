"""
Throughput benchmark.

Measures sustained throughput over a multi-second window with
realistic mixed workload (limit orders + cancels).
"""
import time
import random
import lobx_cpp


def run(duration_s: float = 5.0, seed: int = 42) -> None:
    rng     = random.Random(seed)
    book    = lobx_cpp.OrderBook()
    ops     = 0
    oid     = 0
    active_ids: list[int] = []

    deadline = time.perf_counter() + duration_s
    while time.perf_counter() < deadline:
        if active_ids and rng.random() < 0.2:
            book.cancel(rng.choice(active_ids))
        else:
            order = lobx_cpp.Order(
                oid,
                10_000 + rng.randint(-20, 20),
                rng.randint(1, 20),
                rng.random() < 0.5,
            )
            book.add_limit_order(order)
            active_ids.append(oid)
            oid += 1
            if len(active_ids) > 500:
                active_ids.pop(0)
        ops += 1

    print(f"[throughput] {ops:,} ops in {duration_s:.1f}s → {ops/duration_s:,.0f} ops/s")


if __name__ == "__main__":
    run()
