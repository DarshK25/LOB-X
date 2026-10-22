"""LOB-X API: research results, live paper sim and the C++ matching engine.

    uvicorn main:app --host 127.0.0.1 --port 8000

  /api/v1/research, /api/v1/sessions   stored results and recorded sessions
  /api/v1/sim/*, /ws/sim               one live paper session (the terminal sim)
  /api/v1/book, /api/v1/order, /ws/book   matching engine (needs the C++ build)

If ``frontend/dist`` exists (``npm run build``), the React app is served at /.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from lobx.api.research_routes import router as research_router
from lobx.api.sim_service import router as sim_router
from lobx.api.sim_service import service, sim_stream

logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await service.stop()


app = FastAPI(title="LOB-X", description="Market-making research platform", version="0.3.0",
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(research_router, prefix="/api/v1")
app.include_router(sim_router, prefix="/api/v1")

try:
    from lobx.api.routes import router as engine_router
    from lobx.api.websocket import book_stream
except ImportError:  # C++ extension not built
    engine_router = None
else:
    app.include_router(engine_router, prefix="/api/v1")

    @app.websocket("/ws/book")
    async def websocket_book(websocket: WebSocket) -> None:
        await book_stream(websocket)


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "engine": engine_router is not None, "sim": service.state()["status"]}


@app.websocket("/ws/sim")
async def websocket_sim(websocket: WebSocket) -> None:
    await sim_stream(websocket)


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
