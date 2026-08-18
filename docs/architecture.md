# LOB-X Architecture

## Overview

```
┌─────────────────────┐
│   Frontend (React)  │  WebSocket + REST
└────────┬────────────┘
         │
┌────────▼────────────┐
│  FastAPI (Python)   │  Validation, routing, analytics
└────────┬────────────┘
         │ pybind11 in-process call
┌────────▼────────────┐
│  C++ Engine         │  OrderBook, MatchingEngine (C++20)
└─────────────────────┘
```

## Three-Layer Separation

| Layer | Language | Responsibility |
|-------|----------|----------------|
| Presentation | TypeScript/React | Real-time charts, depth view, P&L |
| Application | Python/FastAPI | Business logic, analytics, risk, models |
| Infrastructure | C++20 | Price-time matching, order resting, cancel |

## Language Boundary

The **only** seam between C++ and Python is the pybind11 module `lobx_cpp`.

- C++ compiles to a shared library: `lobx_cpp.so` / `lobx_cpp.pyd`
- Python imports it like any other module: `import lobx_cpp`
- No serialisation, no network hop, no IPC

## Data Flow — Order Submission

```
Client HTTP POST /api/v1/order
  → FastAPI routes.py: parse OrderRequest schema
  → lobx_cpp.OrderBook.add_limit_order(order)
      → C++ price-time matching
      → returns list[Trade]
  → FastAPI: serialise trades → JSON response
  → WebSocket broadcast: updated book snapshot
```

## Key Design Decisions

1. **pybind11 now** (not ZeroMQ): single runtime, zero serialisation overhead
2. **Static `lobx_core` library**: decouples matching engine from bindings
3. **Price-time priority**: `std::map<Price, PriceLevel>`, FIFO within levels
4. **Order index**: `unordered_map<OrderId, Price>` for O(log N) cancel
5. **Signed Price type**: `int64_t` ticks — avoids floating-point matching bugs
