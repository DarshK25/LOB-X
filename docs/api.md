# API

A small FastAPI service in front of the C++ order book (`lobx_cpp`). It exists to
exercise the engine end to end (REST order entry, live book over a websocket).
The research and simulation code do not go through it.

```bash
python scripts/setup_cpp_build.py           # build lobx_cpp first
cd backend/python && uvicorn main:app --reload
```

Base URL `http://localhost:8000/api/v1`, OpenAPI docs at `/docs`.

## One book

`lobx.api.dependencies.get_order_book()` returns a single cached `OrderBook`.
REST routes receive it through `Depends(get_order_book)` and the websocket reads
the same object, so an order posted over REST is visible on the stream. Every
mutation and every snapshot is taken under one `threading.Lock`: FastAPI runs
sync routes in a thread pool and the C++ book is not thread-safe.

## REST

| Method | Path | Body / result |
|---|---|---|
| GET | `/health` | `{"status": "ok"}` |
| GET | `/book` | `BookSnapshot` |
| POST | `/order` | `OrderRequest` → `list[TradeResponse]` (trades generated immediately) |
| DELETE | `/order/{order_id}` | `{"cancelled": id}`, 404 if the id is not resting |

```json
// OrderRequest: price in ticks, qty in lots, tif GTC | IOC | FOK
{"order_id": 1, "price": 10000, "qty": 5, "is_buy": true, "tif": "GTC"}

// BookSnapshot
{"best_bid": 9998, "best_ask": 10002, "mid_price": 10000.0, "spread": 4,
 "total_bid_qty": 150, "total_ask_qty": 120, "bid_levels": 12, "ask_levels": 10}
```

Time in force follows exchange semantics: `IOC` fills what it can and drops the
rest, `FOK` either fills completely or returns `[]` and leaves the book
untouched, and only `GTC` rests.

## WebSocket

`ws://localhost:8000/ws/book` pushes a `BookSnapshot` at 10 Hz.
