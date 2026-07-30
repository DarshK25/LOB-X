# API Reference

Base URL: `http://localhost:8000/api/v1`

## REST Endpoints

### GET /health
Returns `{"status": "ok"}`.

### GET /book
Returns a `BookSnapshot`:
```json
{
  "best_bid": 9998,
  "best_ask": 10002,
  "mid_price": 10000.0,
  "spread": 4,
  "total_bid_qty": 150,
  "total_ask_qty": 120,
  "bid_levels": 12,
  "ask_levels": 10
}
```

### POST /order
Submit a limit order.

Request body:
```json
{"order_id": 1, "price": 10000, "qty": 5, "is_buy": true, "tif": "GTC"}
```

Response: `list[TradeResponse]` — trades generated immediately.

### DELETE /order/{order_id}
Cancel a resting order. Returns 404 if not found.

## WebSocket

`ws://localhost:8000/ws/book`

Streams `BookSnapshot` JSON at 10 Hz.

## Interactive Docs

FastAPI auto-generates OpenAPI docs at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
