"""Live paper-trading dashboard: all strategies side by side, one account each."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from lobx.execution.simulator import BID
from lobx.live.quote_loop import LiveSimRunner
from lobx.models.hawkes.intensity import regime
from lobx.terminal.dashboard import price_text, quantity_text

SPARK = " ▁▂▃▄▅▆▇█"
REGIME_STYLE = {"QUIET": "dim", "NORMAL": "green", "ELEVATED": "yellow", "BURST": "bold red"}


def sparkline(values: list[float], width: int) -> str:
    if len(values) < 2 or width <= 0:
        return ""
    step = max(1, len(values) // width)
    pts = values[::step][-width:]
    lo, hi = min(pts), max(pts)
    if hi - lo < 1e-12:
        return "▄" * len(pts)
    return "".join(SPARK[1 + int((v - lo) / (hi - lo) * 7.999)] for v in pts)


def money(x: float, nd: int = 4) -> Text:
    style = "green" if x > 0 else "red" if x < 0 else "dim"
    return Text(f"{x:+,.{nd}f}", style=style)


def group_fills(fills, window_ms: int = 1000) -> list[dict]:
    """Collapse one strategy's prints of one sweep into one row.

    One aggressive order prints as many small trades, often at several prices
    one tick apart, and the prints of different strategies are interleaved.
    A row covers one strategy, one side, and every print within ``window_ms``
    of the previous one, whatever the price.
    """
    groups: list[dict] = []
    open_group: dict[tuple, dict] = {}
    for name, fill in fills:
        ts, side, px, qty, _fee, _mid, maker = fill
        key = (name, side, maker)
        prev = open_group.get(key)
        if prev is not None and ts - prev["ts"] <= window_ms:
            prev["qty"] += qty
            prev["n"] += 1
            prev["ts"] = ts
            prev["px_lo"] = min(prev["px_lo"], px)
            prev["px_hi"] = max(prev["px_hi"], px)
            continue
        group = {"name": name, "ts0": ts, "ts": ts, "side": side, "px": px,
                 "px_lo": px, "px_hi": px, "qty": qty, "n": 1, "maker": maker}
        groups.append(group)
        open_group[key] = group
    return groups


def quote_text(order, touch: float, tick: float, now_ms: int) -> Text:
    """Working quote as a price plus how far it sits behind the touch."""
    if order is None or order.remaining <= 1e-12:
        return Text("—", style="dim")
    behind = (touch - order.price) / tick if order.side == BID else (order.price - touch) / tick
    where = "touch" if behind < 0.5 else f"{behind:.0f}t back"
    if order.arrive_at > now_ms:
        where = "sending"
    text = Text(price_text(order.price))
    text.append(f"  {where}", style="dim")
    return text


class SimDashboard:
    def __init__(self, runner: LiveSimRunner, symbol: str, fee_label: str,
                 console: Console | None = None) -> None:
        self.runner = runner
        self.symbol = symbol.upper()
        self.fee_label = fee_label
        self.console = console or Console()
        self.start = time.monotonic()
        self._reports: dict[str, dict] = {}
        self._next_report = 0.0

    # ── panels ──────────────────────────────────────────────────────────────
    def _header(self) -> Panel:
        r = self.runner
        status_style = {"trading": "bold green", "calibrating": "bold yellow"}.get(r.status, "bold yellow")
        el = int(time.monotonic() - self.start)
        t = Text(no_wrap=True, overflow="crop")
        t.append(f"LOB-X PAPER SIM  {self.symbol}", style="bold white")
        t.append(f"  * {r.status.upper()}", style=status_style)
        t.append(f"  {el // 60:02d}:{el % 60:02d}", style="dim")
        cfg = r.config
        size = (f" · ${r.capital_usd:,.0f}/account · quote {r.quote_qty:g} · limit {cfg.max_position:g}"
                if r.quote_qty else f" · ${r.capital_usd:,.0f}/account")
        t.append(f"  · {self.fee_label} · latency {cfg.feed_latency_ms}+{cfg.order_latency_ms} ms"
                 f" · queue {cfg.queue_model}{size}", style="dim")
        if r.recorder is not None:
            t.append(f" · rec {r.recorder.events:,} ev", style="dim magenta")
        return Panel(t, border_style="bright_black")

    def _strategy_panel(self, width: int) -> Panel:
        r = self.runner
        if r.sim is None:
            left = max(0.0, r.warmup_s - r.warmup_elapsed_s)
            msg = Text()
            msg.append("Warm-up. Nothing is quoted yet, so there are no fills.\n", style="yellow")
            msg.append(f"{len(r.recorded_book):,} book updates and {len(r.recorded_trades):,} trades "
                       f"recorded. Quotes start in {left:.0f} s, once sigma, A, k and Hawkes are fitted "
                       "on this window.", style="yellow")
            if r.error:
                msg.append(f"\n{r.error}", style="red")
            return Panel(msg, title="Strategies", border_style="bright_black")
        rows = r.mark_to_market()
        spark_w = max(8, width - 122)
        table = Table(box=None, expand=True, padding=(0, 1))
        for col, just in (("Strategy", "left"), ("Net PnL", "right"), ("Return", "right"),
                          ("Bid", "right"), ("Ask", "right"), ("Position", "right"),
                          ("Fills", "right"), ("Volume", "right"), ("Edge/fill", "right"),
                          ("Adverse 5s", "right"), ("Equity", "left")):
            table.add_column(col, justify=just, no_wrap=True)
        f = r.features
        now, tick = f.last_ts, r.config.tick_size
        best = max((x["equity"] for x in rows), default=0.0)
        for x in rows:
            rep = self._reports.get(x["name"], {})
            name = Text(x["name"], style="bold" if x["equity"] == best and best > 0 else "")
            if x["halted"]:
                name.append(" HALT", style="bold red")
            table.add_row(
                name, money(x["equity"]),
                Text(f"{x['return_bps']:+.2f}bp",
                     style="green" if x["return_bps"] > 0 else "red" if x["return_bps"] < 0 else "dim"),
                quote_text(x["bid"], f.bid, tick, now), quote_text(x["ask"], f.ask, tick, now),
                Text(f"{x['position']:+.5f}", style="cyan" if x["position"] else "dim"),
                str(x["fills"]), f"${x['volume_usd']:,.0f}",
                f"{rep['edge_per_fill_usd']:+.5f}" if rep.get("fills") else "-",
                f"{rep['adverse_bps']:+.2f}bp" if rep.get("fills") else "-",
                Text(sparkline(x["equity_curve"], spark_w),
                     style="green" if x["equity"] >= 0 else "red"))
        return Panel(table, title="Strategies · one account each · same feed",
                     subtitle="Return = net PnL / account capital (1 bp = 0.01%). Bid/Ask are the live "
                              "quotes; '3t back' = three ticks behind the touch. Edge and adverse "
                              "selection refresh every 5 s.",
                     border_style="cyan")

    def _market_panel(self) -> Panel:
        r = self.runner
        f = r.features
        g = Table.grid(expand=True, padding=(0, 1))
        g.add_column(style="dim", no_wrap=True)
        g.add_column(justify="right", no_wrap=True)
        if f.ready:
            mid = 0.5 * (f.bid + f.ask)
            g.add_row("Mid", price_text(mid))
            g.add_row("Spread", f"{f.ask - f.bid:.4g} ({(f.ask - f.bid) / mid * 1e4:.3f} bp)")
            g.add_row("Sigma", f"{f.vol.sigma:.4f} /sqrt(s)")
            lb, ls, ratio = f.intensities(f.last_ts)
            reg = regime(ratio)
            g.add_row("Hawkes λ buy/sell", f"{lb:.2f} / {ls:.2f} /s")
            g.add_row("λ / stationary", Text(f"{ratio:.2f}x {reg}", style=REGIME_STYLE[reg]))
            g.add_row("Flow imbalance", f"{(lb - ls) / (lb + ls) if lb + ls else 0:+.2f}")
            g.add_row("Hawkes model", "MLE fit" if f.calibrated else "default, fitting")
        else:
            g.add_row("Book", "waiting for L2 sync")
        c = r.calibration
        if c:
            g.add_row("Arrival A, k", f"{c['arrival']['A']:.3f}/s, {c['arrival']['k']:.3f}/$")
            g.add_row("Fair value R2", f"{c['fair_value']['r2_in_sample']:.3f}")
        return Panel(g, title="Market & model", border_style="bright_black")

    def _fills_panel(self, rows: int) -> Panel:
        table = Table(box=None, expand=True, padding=(0, 1))
        for col in ("UTC", "Strategy", "Side", "Price", "Qty", "What happened"):
            table.add_column(col, no_wrap=True, justify="right" if col in ("Price", "Qty") else "left")
        shown = group_fills(self.runner.recent_fills)[-max(0, rows - 3):][::-1]
        for g in shown:
            clock = datetime.fromtimestamp(g["ts"] / 1000, timezone.utc).strftime("%H:%M:%S")
            color = "green" if g["side"] == BID else "red"
            label = "BUY" if g["side"] == BID else "SELL"
            if not g["maker"]:
                label += " taker"
            note = "1 print" if g["n"] == 1 else f"{g['n']} prints, one sweep"
            price = price_text(g["px"]) if abs(g["px_hi"] - g["px_lo"]) < 1e-9 \
                else f"{price_text(g['px_lo'])}–{price_text(g['px_hi'])}"
            table.add_row(clock, g["name"][:18], Text(label, style=color), price,
                          quantity_text(g["qty"]), Text(note, style="dim"))
        if not shown:
            waiting = "quotes start after warm-up" if self.runner.sim is None else "no fills yet"
            table.add_row("-", waiting, "", "", "", "")
        return Panel(table, title="Fills · one row per sweep",
                     subtitle="A market order prints as many tiny trades. They are grouped here.",
                     border_style="bright_black")

    def layout(self) -> Layout:
        width, height = self.console.size
        root = Layout()
        root.split_column(Layout(name="header", size=3), Layout(name="body", ratio=1),
                          Layout(name="footer", size=1))
        root["header"].update(self._header())
        n = len(self.runner.accounts) or 3
        top = min(max(6, n + 5), max(6, height - 14))
        root["body"].split_column(Layout(name="strategies", size=top), Layout(name="lower", ratio=1))
        root["strategies"].update(self._strategy_panel(width))
        root["lower"].split_row(Layout(name="market", size=min(46, max(36, width // 3))),
                                Layout(name="fills", ratio=1))
        root["market"].update(self._market_panel())
        root["fills"].update(self._fills_panel(max(3, height - top - 4)))
        foot = Text(no_wrap=True, overflow="crop", style="dim")
        foot.append("Paper trading on the live feed with the backtest simulator · no real orders · Ctrl+C")
        root["footer"].update(foot)
        return root

    async def run(self, max_seconds: float | None = None) -> None:
        ws_task = asyncio.create_task(self.runner.collector.run())
        deadline = None if max_seconds is None else time.monotonic() + max_seconds
        try:
            with Live(self.layout(), console=self.console, screen=False, auto_refresh=False,
                      vertical_overflow="crop") as live:
                while not ws_task.done() and (deadline is None or time.monotonic() < deadline):
                    await asyncio.sleep(0.25)
                    await self.runner.maintain()
                    if time.monotonic() >= self._next_report:
                        self._reports = {x["strategy"]: x for x in self.runner.reports()}
                        self._next_report = time.monotonic() + 5.0
                    live.update(self.layout(), refresh=True)
                if ws_task.done() and not ws_task.cancelled():
                    ws_task.result()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            self.runner.collector.stop()
            ws_task.cancel()
            await asyncio.gather(ws_task, return_exceptions=True)
            await self.runner.collector.close_async()
