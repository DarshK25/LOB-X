#!/usr/bin/env python3
"""Export the real live layout with explicitly labeled synthetic demo trades.

No network, database, or live-market claim: this is only a presentation preview.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from types import SimpleNamespace

_BACKEND = Path(__file__).resolve().parents[1] / "backend" / "python"
sys.path.insert(0, str(_BACKEND))

from lobx.market_data.depth_stream_manager import LocalOrderBook
from lobx.market_data.trade_stream import TradeTick
from lobx.terminal.dashboard import LiveDashboard
from rich.console import Console
from rich.panel import Panel
from rich.terminal_theme import MONOKAI
from rich.text import Text

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>LOB-X terminal demo</title>
<style>
{stylesheet}
body {{ color: {foreground}; background: {background}; padding: 16px; }}
pre, code {{ font-family: 'Cascadia Mono', 'Cascadia Code', 'DejaVu Sans Mono', monospace;
font-size: 14px; line-height: 1.12; font-variant-ligatures: none; }}
</style></head><body><pre><code>{code}</code></pre></body></html>"""


class PreviewFeed:
    """In-memory event source confined to this demo script."""

    ws_status = "Connected"

    def __init__(self):
        self.depth_mgr = SimpleNamespace(book=LocalOrderBook(), is_synced=True)

    def on_book_update(self, callback):
        self.book_callback = callback

    def on_trade(self, callback):
        self.trade_callback = callback


def demo_layout(console: Console, mode: str = "candle", scenario: str = "quiet"):
    if scenario not in ("quiet", "moving", "breakout"):
        raise ValueError("scenario must be quiet, moving, or breakout")
    feed = PreviewFeed()
    dashboard = LiveDashboard("BTCUSDT", bucket_s=5, mode=mode, collector=feed, console=console)
    end = int(time.time() // 5) * 5000
    for bucket in range(50):
        if scenario == "moving":
            center = 81710 + 8 * math.sin(bucket * .23) + 2 * math.cos(bucket * .8)
            change = 2.4 * math.sin(bucket * .9)
            prices = (center, center + abs(change) + .8, center - abs(change) - .5,
                      center + change * .5, center + change)
        else:
            base = 81588.35 + (max(0, bucket - 43) * .6 if scenario == "breakout" else 0)
            prices = (base + (.01 if bucket % 2 else 0), base + .01, base,
                      base + (.01 if bucket % 3 else 0), base + (0 if bucket % 2 else .01))
        for offset, price in enumerate(prices):
            if scenario == "moving":
                qty = ((.04 + abs(math.sin(bucket + offset)) * .3)
                       * (.2 + (bucket % 9) * .2))
            else:
                qty = 1.27706 / 5 if bucket in (20, 47) else .00058 * (1 + bucket % 3)
            tick = TradeTick(end - (49 - bucket) * 5000 + offset * 150,
                             bucket * 5 + offset, round(price, 2),
                             round(qty, 8),
                             bool((bucket + offset) % 3), "BTCUSDT")
            feed.trade_callback(tick)
    last = dashboard.chart.candles[-1].close
    for level in range(10):
        if scenario == "moving":
            bid_qty, ask_qty = .2 + .3 * (level % 4), .1 + .18 * (level % 3)
        else:
            bid_qty = 2.0065 if level == 0 else (.00007 if level == 4 else .0009 * level)
            ask_qty = 5.6663 if level == 0 else .00013 * level ** 2
        feed.depth_mgr.book.bids[round(last - level * .05, 2)] = round(bid_qty, 8)
        feed.depth_mgr.book.asks[round(last + .01 + level * .05, 2)] = round(ask_qty, 8)
    feed.book_callback({"E": int(time.time() * 1000)})
    layout = dashboard.generate_layout()
    layout["header"].update(Panel(Text(f"DEMO · {scenario.upper()} · synthetic trades, NOT live · LOB-X BTCUSDT",
                                      style="bold yellow", no_wrap=True, overflow="crop"),
                                  border_style="yellow"))
    return layout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/live-terminal-preview.html"))
    parser.add_argument("--width", type=int, default=160)
    parser.add_argument("--height", type=int, default=42)
    parser.add_argument("--mode", choices=("candle", "line"), default="candle")
    parser.add_argument("--scenario", choices=("quiet", "moving", "breakout"), default="quiet")
    args = parser.parse_args()
    if not args.output.parent.is_dir():
        parser.error("Output parent must already exist")
    if args.width < 40 or args.height < 20:
        parser.error("Use at least 40 columns and 20 rows")
    console = Console(width=args.width, height=args.height, record=True, color_system="truecolor")
    console.print(demo_layout(console, args.mode, args.scenario))
    console.save_html(str(args.output), inline_styles=True, theme=MONOKAI, code_format=_HTML)
    print(f"Saved labeled DEMO preview: {args.output}")


if __name__ == "__main__":
    main()
