"""Probabilistic queue-position model for a resting order (L1/L2 data).

Public market data shows the total size at a price, not where our order sits
inside it. Following Rigtorp (2013) and the hftbacktest ``ProbQueueModel``:

* On joining a level we are at the back: ``front = displayed size``.
* A trade at our price consumes the queue ahead of us first.
* A size *decrease* not explained by trades is a cancellation somewhere in the
  queue. The share that happened ahead of us is ``1 - p`` with
  ``p = 1 - (front / (front + back))**n``: when we are near the back most
  cancellations are ahead of us; near the front most are behind.
* A size *increase* is new orders joining behind us.

References:
  https://rigtorp.se/2013/06/08/estimating-order-queue-position.html
  hftbacktest: src/backtest/models/queue.rs (PowerProbQueueFunc3)
"""
from __future__ import annotations


def advance_on_depth_change(front: float, prev_qty: float, new_qty: float,
                            cum_trade_qty: float, n: float = 3.0) -> float:
    """Return the new quantity ahead of us after the level changes size."""
    change = prev_qty - new_qty - cum_trade_qty
    if change < 0:
        return min(front, new_qty)
    back = max(prev_qty - front, 0.0)
    total = front + back
    prob = 1.0 - (front / total) ** n if total > 0 else 1.0
    est_front = front - (1.0 - prob) * change + min(back - prob * change, 0.0)
    return max(0.0, min(est_front, new_qty))
