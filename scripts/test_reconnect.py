"""Exercise reconnect recovery through Collector and DepthStreamManager."""
import asyncio
import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "python"))

from lobx.market_data.collector import Collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def wait_for(predicate, timeout_s: float, description: str) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    print(f"Timed out waiting for {description} after {timeout_s:.1f}s")
    return False


async def main() -> int:
    # A temporary output directory prevents the verification run from changing
    # the production market-data dataset.
    with tempfile.TemporaryDirectory(prefix="lobx-reconnect-") as data_dir:
        collector = Collector(
            symbol="BTCUSDT",
            data_dir=data_dir,
            enable_engine_mirror=False,
            flush_every=10_000,
            heartbeat_s=3600,
        )
        run_task = asyncio.create_task(collector.run())
        try:
            initially_synced = await wait_for(
                lambda: collector.depth_mgr.is_synced,
                30,
                "initial depth snapshot sync",
            )
            print(f"Synced before forced drop : {initially_synced}")
            if not initially_synced:
                return 1

            print("Forcing Collector WebSocket disconnect...")
            await collector.force_disconnect()
            saw_desync = await wait_for(
                lambda: not collector.depth_mgr.is_synced,
                10,
                "depth manager to become desynced",
            )
            print(f"Desync observed after drop : {saw_desync}")
            resynced = await wait_for(
                lambda: saw_desync and collector.depth_mgr.is_synced,
                35,
                "fresh depth snapshot sync",
            )
            print(f"Resynced after reconnect   : {resynced}")
            print("VERDICT: " + ("PASS" if saw_desync and resynced else "FAIL"))
            return 0 if saw_desync and resynced else 1
        finally:
            collector.stop()
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            collector.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
