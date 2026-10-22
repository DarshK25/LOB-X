# C++ matching engine

`backend/cpp`: a price-time priority limit order book in C++20, exposed to
Python with pybind11 as `lobx_cpp`. Prices are integer ticks, quantities integer
lots: no floating point on the matching path.

## Data structures

```
OrderBook
├── bids_         std::map<Price, PriceLevel, std::greater<>>   best bid = begin()
├── asks_         std::map<Price, PriceLevel>                   best ask = begin()
└── order_index_  std::unordered_map<OrderId, Location{price, side, list iterator}>

PriceLevel
├── orders_       std::list<Order>     FIFO; iterators stay valid on erase
└── total_qty_    cached sum, so depth queries never scan the queue
```

The index stores the order's iterator inside its level, so a cancel is one hash
lookup, one `map::find` for the level and an O(1) `list::erase`. The first
version stored only the price and scanned the level's `deque` to find the
order. That made cancel O(queue length), and p99 was 137 µs in the native
benchmark.

## Matching

```
add_limit_order(order):
  if FOK and fillable_qty(order) < order.qty:  return {}      # book untouched
  while order.remaining > 0 and the opposite best price crosses:
      passive = front of best level
      fill = min(order.remaining, passive.remaining)
      trade at the passive price (the resting order sets the price)
      if the passive order is done: remove it from the index
      reduce_front(fill)                                       # may pop it
      drop the level if it is empty
  if order.remaining > 0 and tif == GTC: rest it
```

`fillable_qty` walks the opposite side up to the limit price and stops as soon
as the order would be covered, so the FOK pre-check costs O(levels touched).

### Bugs fixed

| Bug | Effect | Fix |
|---|---|---|
| FOK traded first and checked later | A FOK order that could not complete still took liquidity, which violates FOK | `fillable_qty` pre-check, no trades if short |
| `passive` reference used after `reduce_front` popped it | Use-after-free on every full fill (index erase read freed memory) | copy the id and the done flag before `reduce_front` |
| Linear cancel | p99 137 µs | iterator index, O(1) erase |

Regression tests in `tests/test_matching.cpp` cover these: insufficient
liquidity, liquidity only beyond the limit, an exact fill, and fully filled
orders leaving the index. The original code fails the FOK tests.

## Complexity (L = number of price levels)

| Operation | Cost |
|---|---|
| Add, no match | O(log L) |
| Add, sweeping k levels with f fills | O(k log L + f) |
| FOK pre-check | O(k) |
| Cancel | O(1) average + O(log L) level lookup |
| Best bid / ask, level qty | O(1) |

## Build, test, benchmark

```bash
python scripts/setup_cpp_build.py --tests --bench
```

The script uses CMake when CMake and a compiler are on PATH. Otherwise it uses
Zig's bundled clang (`pip install ziglang`), so Windows needs no Visual Studio.
Catch2 is fetched as the amalgamated single file. Current status: 25 test cases,
84 assertions, all passing.

### Native benchmark

`bench/bench_orderbook.cpp` sends 2M operations to the book: limit orders within
±20 ticks of a mid, with 20% of operations cancelling recent orders. About 40
levels stay active and crosses are frequent. Each call is timed with
`steady_clock`.

| | p50 | p90 | p99 | p99.9 |
|---|---|---|---|---|
| `add_limit_order` | 0.3 µs | 0.7 µs | 1.4 µs | 4.1 µs |
| `cancel` | 0.1 µs | 0.2 µs | 0.5 µs | 0.9 µs |

That is 1.77M ops/s single-threaded, built with Zig clang `-O3` on a Windows
laptop. Windows `steady_clock` ticks every 100 ns, so the cancel percentiles
are at the clock's resolution.

Through Python (`scripts/benchmark.py`, one pybind11 call per order):
612k orders/s, p50 1.8 µs and p99 9.3 µs. The Python call overhead is about
1.5 µs per order.

### What this is not

The book is single-threaded and allocates one list node per order. A production
engine would use a pool allocator or intrusive lists, preallocated price
arrays for a bounded tick range, and a lock-free inbound queue. There is no
self-trade prevention, no market or stop orders, and no order amend.
