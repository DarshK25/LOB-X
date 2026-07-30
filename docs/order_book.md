# Order Book

## Data Structures

```
OrderBook
├── bids_: std::map<Price, PriceLevel, std::greater<>>
│           └── PriceLevel: std::deque<Order>
├── asks_: std::map<Price, PriceLevel>
│           └── PriceLevel: std::deque<Order>
└── order_index_: std::unordered_map<OrderId, Price>
```

## PriceLevel

A `std::deque<Order>` giving O(1) front-access and O(1) pop.
Cached `total_qty_` field avoids linear scan for depth queries.

## Order Fields

| Field | Type | Description |
|-------|------|-------------|
| id | uint64_t | Unique order identifier |
| price | int64_t | Limit price in ticks |
| qty | uint64_t | Original quantity |
| qty_remaining | uint64_t | Unfilled quantity |
| side | Side enum | Buy / Sell |
| tif | TimeInForce | GTC / IOC / FOK |
| timestamp | steady_clock | For latency measurement |

## Invariants

1. All prices in bids < all prices in asks (no locked book)
2. order_index_ is consistent with bids_ and asks_
3. PriceLevel removed from map when empty after cancel/fill
