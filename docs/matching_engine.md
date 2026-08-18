# Matching Engine

## Algorithm

Price-time priority (FIFO within price levels).

- **Bids**: `std::map<Price, PriceLevel, std::greater<>>` — highest price first
- **Asks**: `std::map<Price, PriceLevel>` — lowest price first
- Each `PriceLevel` is a `std::deque<Order>` (FIFO, O(1) front access)
- `order_index_`: `unordered_map<OrderId, Price>` for O(log N) cancel

## Matching Loop

```
add_limit_order(aggressor):
  while aggressor has qty AND opposite side non-empty:
    best_passive = opposite.begin()
    if price doesn't cross: break
    fill front-of-queue at passive price (price improvement)
    record Trade(buy_id, sell_id, fill_price, fill_qty)
  if qty_remaining > 0 and TIF == GTC: rest(aggressor)
```

## Time Complexity

| Operation | Complexity |
|-----------|-----------|
| Add (no match) | O(log N) |
| Add (match k levels) | O(k log N + fills) |
| Cancel | O(log N) |
| Best bid/ask | O(1) |

## Tests

See `backend/cpp/tests/`:
- `test_orderbook.cpp` — book state, spread, qty
- `test_matching.cpp` — full/partial fill, multi-level sweep, price-time priority
- `test_cancel.cpp` — single/double cancel, level cleanup, post-fill cancel
