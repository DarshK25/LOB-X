# API

FastAPI service behind the React frontend (`backend/python/main.py`). It
serves stored research results, recorded sessions and replays, one live paper
session, and the C++ order book.

```bash
cd backend/python && uvicorn main:app --port 8000
```

Base URL `http://localhost:8000/api/v1`, OpenAPI docs at `/docs`. If
`frontend/dist` exists (`npm run build` in `frontend/`), the app is served at
`/`. CORS allows the Vite dev server on port 5173. There is no authentication;
bind to localhost.

## Research and sessions

| Method | Path | Result |
|---|---|---|
| GET | `/health` | `{"status", "engine": bool, "sim": status}` |
| GET | `/research` | one row per `results/research/*.json`: id, window, venue, hours, fee tiers |
| GET | `/research/{id}` | the full result: calibration, γ grid, out-of-sample reports, bootstrap, stress |
| GET | `/fees` | fee profiles |
| GET | `/sessions` | recorded sessions in `data/sessions` (newest first) |
| GET | `/sessions/{file}/replay?fee=` | replay; per account: net PnL, fees, fills, return, equity curve, recorded vs replayed fill counts and the first mismatch |

NaN and infinity are sent as `null`. Paths are confined to their directories.

## Live paper session

| Method | Path | Body / result |
|---|---|---|
| POST | `/sim/start` | `{"symbol": "BTCUSDT", "market": "futures", "fee": "mm_rebate", "capital_usd": 10000, "warmup_s": 120, "record": true}` → state; `market` is `spot` or `futures`; 409 if one is running |
| POST | `/sim/stop` | state (the recording is closed with a summary line) |
| GET | `/sim/state` | state |
| WS | `/ws/sim` | state at 2 Hz |

The session is the terminal's `LiveSimRunner` built by `lobx.live.setup.build_live_sim`,
so both front ends trade and record identically. State contains:

* `status` (`idle`, `warming up`, `calibrating`, `trading`, `stopped`), `error`
* fee tier and bp, capital, quote size, position limit, tick, latency
* `warmup`: elapsed and target seconds, recorded book and trade counts
* `recording`: file name and events written
* `market`: bid, ask, mid, spread (bp), σ, Hawkes λ per side, ratio and regime
* `calibration`: σ, A, k, fair-value R²
* `accounts`: per strategy equity, return (bp), position, fills, volume, fees,
  working bid and ask (price, size, ticks behind the touch, in flight, queue
  ahead), thinned equity curve, edge per fill, adverse selection, Sharpe, drawdown
* `fills`: the last 60 fills grouped by sweep, newest first
* `mid_history`: the last 600 mids (one per 100 ms)

## Matching engine

Registered only when `lobx_cpp` is importable (`python scripts/setup_cpp_build.py`).

`lobx.api.dependencies.get_order_book()` returns a single cached `OrderBook`.
REST routes receive it through `Depends(get_order_book)` and the websocket reads
the same object, so an order posted over REST is visible on the stream. Every
mutation and every snapshot is taken under one `threading.Lock`: FastAPI runs
sync routes in a thread pool and the C++ book is not thread-safe.

| Method | Path | Body / result |
|---|---|---|
| GET | `/book` | `BookSnapshot` |
| POST | `/order` | `OrderRequest` → `list[TradeResponse]` (trades generated immediately) |
| DELETE | `/order/{order_id}` | `{"cancelled": id}`, 404 if the id is not resting |
| WS | `/ws/book` | `BookSnapshot` at 10 Hz |

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
