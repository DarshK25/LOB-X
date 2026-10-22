#!/usr/bin/env python3
"""LOB-X interactive terminal.

Commands (type ``help`` inside the CLI):
  live     live market view: candles, L2 book, tape, Hawkes order-flow gauge
  sim      paper-trade every strategy on the live feed, one account each
  backtest research protocol on a stored history window (calibrate, tune, OOS)
  quote    AS and GLFT quotes for a given inventory from a calibration
  scan     spread vs fee screen across Binance USD-M futures symbols
  book     one L2 snapshot
  status   environment check
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "python"))

from rich.console import Console  # noqa: E402
from rich.prompt import Prompt  # noqa: E402
from rich.table import Table  # noqa: E402

from lobx.backtest import research  # noqa: E402
from lobx.backtest.data import list_history, load_history  # noqa: E402
from lobx.execution.fees import FEE_PROFILES  # noqa: E402
from lobx.execution.simulator import ExecutionConfig  # noqa: E402
from lobx.models.glft import glft_coefficients, glft_depths  # noqa: E402
from lobx.models.avellaneda.optimal_spread import optimal_spread  # noqa: E402
from lobx.models.avellaneda.reservation_price import reservation_price  # noqa: E402

for name in ("lobx", "httpx", "websockets"):
    logging.getLogger(name).setLevel(logging.ERROR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1252 consoles cannot print the UI glyphs
CONSOLE = Console()
RESULTS = ROOT / "results" / "research"

LOGO = """[bold green]
 ██╗      ██████╗ ██████╗       ██╗  ██╗
 ██║     ██╔═══██╗██╔══██╗      ╚██╗██╔╝
 ██║     ██║   ██║██████╔╝ ████╗ ╚███╔╝
 ██║     ██║   ██║██╔══██╗ ╚═══╝ ██╔██╗
 ███████╗╚██████╔╝██████╔╝      ██╔╝ ██╗
 ╚══════╝ ╚═════╝ ╚═════╝       ╚═╝  ╚═╝[/bold green]
[bold white]Market-making research platform[/bold white] [dim]· C++ matching engine · calibrated GLFT/AS · Hawkes · queue-aware simulator[/dim]
"""


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "lobx"})
    with urllib.request.urlopen(req, timeout=8.0) as resp:
        return json.loads(resp.read().decode())


DEFAULT_GAMMA = 0.01


def _latest_calibration(fee: str = "vip0") -> dict | None:
    """Calibration plus tuned gammas from a single-window research result.

    Calm windows are preferred (the normal state of the market), newest first.
    """
    files = sorted(RESULTS.glob("*.json"),
                   key=lambda p: ("calm" not in p.name, -p.stat().st_mtime))
    for path in files:
        res = json.loads(path.read_text())
        if "calibration" not in res or res["window"].startswith("cross"):
            continue
        tuning = res["fees"].get(fee, next(iter(res["fees"].values()), {})).get("tuning", {})
        return res["calibration"] | {
            "_source": path.name,
            "_gamma_as": tuning.get("AS", DEFAULT_GAMMA),
            "_gamma_glft": tuning.get("GLFT", DEFAULT_GAMMA),
        }
    return None


class LobxCLI:
    def __init__(self) -> None:
        self.running = True

    def run_command(self, line: str) -> None:
        parts = line.strip().split()
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]
        handlers = {
            "live": self.cmd_live, "sim": self.cmd_sim, "backtest": self.cmd_backtest,
            "bt": self.cmd_backtest, "quote": self.cmd_quote, "as": self.cmd_quote,
            "scan": self.cmd_scan, "book": self.cmd_book, "status": self.cmd_status,
            "help": self.cmd_help, "?": self.cmd_help,
        }
        if cmd in ("exit", "quit", "q"):
            self.running = False
            return
        handler = handlers.get(cmd)
        if handler is None:
            CONSOLE.print(f"[red]Unknown command '{cmd}'. Type help.[/red]")
            return
        try:
            handler(args)
        except KeyboardInterrupt:
            pass
        except Exception as exc:  # noqa: BLE001 - keep the REPL alive
            CONSOLE.print(f"[bold red]{cmd} failed:[/bold red] {type(exc).__name__}: {exc}")

    def cmd_help(self, _args=None) -> None:
        t = Table(title="LOB-X commands", header_style="bold cyan")
        t.add_column("Command", style="bold yellow", no_wrap=True)
        t.add_column("What it does")
        t.add_row("live [SYM] [5s|1m] [candle|line]", "Live market view with the calibrated Hawkes gauge (online MLE refit every 60 s).")
        t.add_row("sim [SYM] [FEE] [WARMUP_S] [RUN_S]", "Paper-trade all strategies on the live feed, one account each. "
                  f"FEE in {', '.join(FEE_PROFILES)} (default spot_vip0). Calibrates on the first WARMUP_S seconds "
                  "(default 120); stops after RUN_S seconds or Ctrl+C.")
        t.add_row("backtest [WINDOW] [FEE]", f"Calibrate + tune in-sample, compare strategies out-of-sample. Windows: {', '.join(list_history()) or 'none (run scripts/fetch_history.py)'}.")
        t.add_row("quote [SYM] [q_lots]", "AS and GLFT quotes for inventory q (lots of 0.001) from the latest calibration and the live futures book.")
        t.add_row("scan [N]", "Rank Binance USD-M futures symbols by quoted spread vs the VIP0 maker fee.")
        t.add_row("book [SYM]", "One spot L2 snapshot.")
        t.add_row("status", "C++ engine, data and results availability.")
        t.add_row("exit", "Leave.")
        CONSOLE.print(t)

    # ── live views ──────────────────────────────────────────────────────────
    def cmd_live(self, args: list[str]) -> None:
        from lobx.terminal.dashboard import LiveDashboard

        symbol, bucket_s, mode = "BTCUSDT", 5, "candle"
        for a in (x.lower() for x in args):
            if a in ("line", "candle"):
                mode = a
            elif m := re.fullmatch(r"([0-9]+)([sm])", a):
                bucket_s = int(m[1]) * (60 if m[2] == "m" else 1)
            else:
                symbol = a.upper()
        dash = LiveDashboard(symbol, bucket_s=bucket_s, mode=mode, console=CONSOLE)
        asyncio.run(dash.run())
        if dash._run_error:
            CONSOLE.print(f"[red]{dash._run_error}[/red]")

    def cmd_sim(self, args: list[str]) -> None:
        from lobx.live.quote_loop import LiveSimRunner
        from lobx.market_data.collector import Collector
        from lobx.terminal.sim_dashboard import SimDashboard

        symbol, fee = "BTCUSDT", "spot_vip0"
        numbers = []
        for a in args:
            if a.lower() in FEE_PROFILES:
                fee = a.lower()
            elif _is_number(a):
                numbers.append(float(a))
            else:
                symbol = a.upper()
        warmup = numbers[0] if numbers else 120.0
        duration = numbers[1] if len(numbers) > 1 else None
        prof = FEE_PROFILES[fee]
        tick = self._spot_tick(symbol)
        cfg = ExecutionConfig(tick_size=tick, maker_fee=prof["maker"], taker_fee=prof["taker"],
                              max_position=0.01)

        tuned = _latest_calibration() or {}
        g_as = tuned.get("_gamma_as", DEFAULT_GAMMA)
        g_glft = tuned.get("_gamma_glft", DEFAULT_GAMMA)

        def build(calib: dict):
            return research.strategy_suite(calib, tick, gamma_as=g_as, gamma_glft=g_glft,
                                           maker_fee=prof["maker"])

        collector = Collector(symbol=symbol, enable_engine_mirror=False, enable_storage=False,
                              heartbeat_s=60)
        runner = LiveSimRunner(collector, build, cfg, warmup_s=warmup)
        asyncio.run(SimDashboard(runner, symbol, prof["label"], console=CONSOLE).run(duration))
        self._print_reports(runner.reports(), f"Live paper session · {symbol} · {prof['label']}")

    @staticmethod
    def _spot_tick(symbol: str) -> float:
        info = _get_json(f"https://api.binance.com/api/v3/exchangeInfo?symbol={symbol}")
        for f in info["symbols"][0]["filters"]:
            if f["filterType"] == "PRICE_FILTER":
                return float(f["tickSize"])
        raise ValueError(f"no tick size for {symbol}")

    # ── research ────────────────────────────────────────────────────────────
    def cmd_backtest(self, args: list[str]) -> None:
        windows = list_history()
        if not windows:
            CONSOLE.print("[red]No history. Run: python scripts/fetch_history.py[/red]")
            return
        window = next((a for a in args if a in windows), windows[0])
        fee = next((a for a in args if a in FEE_PROFILES), "vip0")
        CONSOLE.print(f"[bold magenta]Backtest {window} · {FEE_PROFILES[fee]['label']}[/bold magenta]")
        CONSOLE.print("[dim]Calibrating on the first half, choosing gamma in-sample, "
                      "evaluating every strategy on the second half…[/dim]")
        res = research.evaluate_window(load_history(window), (fee,),
                                       log=lambda m: CONSOLE.print(f"[dim]{m}[/dim]"))
        path = research.save_result(res, RESULTS)
        block = res["fees"][fee]
        self._print_reports(block["oos"], f"Out-of-sample · {window}", block["bootstrap"])
        CONSOLE.print(f"[dim]Saved {path}[/dim]")

    @staticmethod
    def _print_reports(reports: list[dict], title: str, boot: dict | None = None) -> None:
        if not reports:
            CONSOLE.print("[yellow]No strategy results (session ended before calibration).[/yellow]")
            return
        t = Table(title=title, header_style="bold cyan")
        cols = ["Strategy", "Net $", "95% CI", "Fills", "Fills/h", "Edge/fill $", "Adverse 5s bp",
                "Sharpe", "Max DD $", "Break-even bp"]
        for c in cols:
            t.add_column(c, justify="left" if c == "Strategy" else "right", no_wrap=True)
        for r in reports:
            b = (boot or {}).get(r["strategy"], {})
            ci = b.get("pnl_ci")
            be = r.get("breakeven_maker_fee_bps")
            net_style = "green" if r["net_pnl"] > 0 else "red" if r["net_pnl"] < 0 else "dim"
            t.add_row(r["strategy"], f"[{net_style}]{r['net_pnl']:+.4f}[/{net_style}]",
                      f"[{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "-", str(r["fills"]),
                      f"{r['fills_per_hour']:.0f}", f"{r['edge_per_fill_usd']:+.5f}",
                      f"{r['adverse_bps']:+.2f}", f"{r['sharpe']:+.1f}", f"{r['max_drawdown']:.4f}",
                      f"{be:+.2f}" if be is not None else "-")
        CONSOLE.print(t)

    def cmd_quote(self, args: list[str]) -> None:
        symbol = next((a.upper() for a in args if not _is_number(a)), "BTCUSDT")
        q = next((float(a) for a in args if _is_number(a)), 0.0)
        calib = _latest_calibration()
        if calib is None:
            CONSOLE.print("[red]No calibration yet. Run backtest or scripts/run_research.py.[/red]")
            return
        bt = _get_json(f"https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol={symbol}")
        bid, ask = float(bt["bidPrice"]), float(bt["askPrice"])
        mid = 0.5 * (bid + ask)
        sigma, A, k = calib["sigma"], calib["arrival"]["A"], calib["arrival"]["k"]
        g_as, g_glft, horizon = calib["_gamma_as"], calib["_gamma_glft"], research.AS_HORIZON_S
        r = reservation_price(mid, q, g_as, sigma, horizon)
        as_spread = optimal_spread(g_as, sigma, horizon, k)
        bd, ad, hs, skew = glft_depths(sigma, q, glft_coefficients(g_glft, k, A))
        t = Table(title=f"Model quotes · {symbol} futures · q = {q:+g} lots · "
                        f"gamma AS {g_as:g} / GLFT {g_glft:g} per USD (tuned in-sample)",
                  header_style="bold cyan")
        t.add_column("Quantity", style="bold white")
        t.add_column("Value", justify="right")
        t.add_column("Note", style="dim")
        t.add_row("Mid / spread", f"{mid:,.2f} / {ask - bid:.2f}", "live bookTicker")
        t.add_row("sigma", f"{sigma:.3f} USD/sqrt(s)", f"calibrated · {calib['_source']}")
        t.add_row("A, k", f"{A:.3f}/s, {k:.3f}/USD", "lambda(d) = A exp(-k d)")
        t.add_row("AS reservation price", f"{r:,.2f}", "r = s - q gamma sigma^2 tau, tau = 60 s")
        t.add_row("AS bid / ask", f"{r - as_spread / 2:,.2f} / {r + as_spread / 2:,.2f}",
                  f"spread {as_spread:.2f}")
        t.add_row("GLFT bid / ask", f"{mid - bd:,.2f} / {mid + ad:,.2f}",
                  f"half-spread {hs:.2f}, skew {skew:.3f}/lot")
        t.add_row("Skew direction", "long -> lower quotes" if q > 0 else "short -> higher quotes"
                  if q < 0 else "flat -> symmetric", "both models sell inventory down")
        CONSOLE.print(t)

    def cmd_scan(self, args: list[str]) -> None:
        n = int(args[0]) if args and args[0].isdigit() else 20
        rows = _get_json("https://fapi.binance.com/fapi/v1/ticker/bookTicker")
        maker_bp = FEE_PROFILES["vip0"]["maker"] * 1e4
        out = []
        for r in rows:
            b, a = float(r["bidPrice"]), float(r["askPrice"])
            if b <= 0 or a <= b or not r["symbol"].endswith("USDT"):
                continue
            half_bp = (a - b) / (a + b) * 1e4
            out.append((r["symbol"], half_bp, half_bp / maker_bp))
        out.sort(key=lambda x: -x[2])
        t = Table(title=f"Half-spread vs VIP0 maker fee ({maker_bp:.1f} bp) · top {n}",
                  header_style="bold cyan")
        for c in ("Symbol", "Half-spread bp", "Half-spread / fee"):
            t.add_column(c, justify="right" if c != "Symbol" else "left")
        for sym, hb, ratio in out[:n]:
            style = "green" if ratio >= 1 else "yellow" if ratio >= 0.5 else "dim"
            t.add_row(sym, f"{hb:.2f}", f"[{style}]{ratio:.2f}[/{style}]")
        btc = next((x for x in out if x[0] == "BTCUSDT"), None)
        CONSOLE.print(t)
        if btc:
            CONSOLE.print(f"[dim]BTCUSDT half-spread {btc[1]:.3f} bp = {btc[2]:.3f}x the maker fee: "
                          f"spread capture alone cannot pay VIP0 fees on BTC.[/dim]")
        CONSOLE.print("[dim]A wide spread is usually compensation for thin, toxic flow; this is a "
                      "screen for where fees are not the binding constraint, not a recommendation.[/dim]")

    def cmd_book(self, args: list[str]) -> None:
        symbol = (args[0] if args else "BTCUSDT").upper()
        data = _get_json(f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=10")
        t = Table(title=f"{symbol} spot L2", header_style="bold cyan")
        for c in ("Side", "Price", "Qty"):
            t.add_column(c, justify="right")
        for p, q in reversed(data["asks"][:5]):
            t.add_row("[red]ASK[/red]", p, q)
        for p, q in data["bids"][:5]:
            t.add_row("[green]BID[/green]", p, q)
        CONSOLE.print(t)

    def cmd_status(self, _args=None) -> None:
        t = Table(title="LOB-X status", header_style="bold cyan")
        t.add_column("Component", style="bold white")
        t.add_column("State")
        try:
            import lobx_cpp  # noqa: F401
            t.add_row("C++ matching engine", "[green]built (lobx_cpp importable)[/green]")
        except ImportError:
            t.add_row("C++ matching engine", "[yellow]not built: python scripts/setup_cpp_build.py[/yellow]")
        t.add_row("History windows", ", ".join(list_history()) or "[yellow]none[/yellow]")
        t.add_row("Research results", str(len(list(RESULTS.glob("*.json")))) + " windows")
        cal = _latest_calibration()
        t.add_row("Latest calibration", cal["_source"] if cal else "[yellow]none[/yellow]")
        CONSOLE.print(t)

    def loop(self) -> None:
        CONSOLE.print(LOGO)
        CONSOLE.print("Type [bold yellow]help[/bold yellow] for commands.\n")
        while self.running:
            try:
                self.run_command(Prompt.ask("[bold cyan]lobx[/bold cyan]"))
            except (KeyboardInterrupt, EOFError):
                self.running = False


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    cli = LobxCLI()
    if len(sys.argv) > 1:
        cli.run_command(" ".join(sys.argv[1:]))
    else:
        cli.loop()
