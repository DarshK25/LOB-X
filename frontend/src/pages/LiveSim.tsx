import { useEffect, useState } from "react";
import { colorFor, fmt, postJSON, Quote, SimAccount, SimState, tone, useSimState } from "../api";
import { LineChart, Sparkline } from "../charts";

const FEES = ["spot_vip0", "vip0", "vip9", "mm_rebate", "zero"];

function QuoteCell({ q }: { q: Quote | null }) {
  if (!q) return <span className="dim">—</span>;
  const where = q.sending ? "sending" : q.ticks_behind < 0.5 ? "touch" : `${q.ticks_behind.toFixed(0)}t back`;
  return (
    <span>
      {fmt.price(q.px)} <span className={q.sending ? "warn small" : "dim small"}>{where}</span>
    </span>
  );
}

function Controls({ state }: { state: SimState | null }) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [market, setMarket] = useState<"spot" | "futures">("spot");
  const [fee, setFee] = useState("spot_vip0");
  const [capital, setCapital] = useState(10000);
  const [warmup, setWarmup] = useState(120);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const running = state && ["warming up", "calibrating", "trading"].includes(state.status);
  useEffect(() => {
    if (!running || !state) return;
    if (state.symbol) setSymbol(state.symbol);
    if (state.market_type) setMarket(state.market_type);
    if (state.fee) setFee(state.fee);
    if (state.capital_usd) setCapital(state.capital_usd);
    if (state.warmup) setWarmup(state.warmup.target_s);
  }, [running, state?.symbol, state?.market_type, state?.fee, state?.capital_usd, state?.warmup?.target_s]);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card controls">
      <label>Symbol<input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} disabled={!!running} /></label>
      <label>Market
        <select value={market} disabled={!!running} onChange={(e) => {
          const value = e.target.value as "spot" | "futures";
          setMarket(value);
          setFee(value === "spot" ? "spot_vip0" : "vip0");
        }}>
          <option value="spot">Spot</option>
          <option value="futures">USD-M futures</option>
        </select>
      </label>
      <label>Fees
        <select value={fee} onChange={(e) => setFee(e.target.value)} disabled={!!running}>
          {(state?.fees ?? FEES).map((f) => <option key={f}>{f}</option>)}
        </select>
      </label>
      <label>Capital / account ($)<input type="number" min={100} step={1000} value={capital} onChange={(e) => setCapital(+e.target.value)} disabled={!!running} /></label>
      <label>Warm-up (s)<input type="number" min={20} max={3600} value={warmup} onChange={(e) => setWarmup(+e.target.value)} disabled={!!running} /></label>
      {running ? (
        <button className="danger" disabled={busy} onClick={() => act(() => postJSON("/api/v1/sim/stop"))}>Stop</button>
      ) : (
        <button disabled={busy} onClick={() => act(() => postJSON("/api/v1/sim/start", { symbol, market, fee, capital_usd: capital, warmup_s: warmup }))}>
          {busy ? "Starting…" : "Start session"}
        </button>
      )}
      {err && <span className="neg small">{err}</span>}
    </div>
  );
}

function Economics({ s }: { s: SimState }) {
  const m = s.market;
  if (!m || s.maker_fee_bps == null) return null;
  const half = m.spread_bps / 2;
  const fee = s.maker_fee_bps;
  const quoteUsd = (s.quote_qty ?? 0) * m.mid;
  const ok = half > fee;
  return (
    <div className={`card callout ${ok ? "ok" : "bad"}`}>
      <b>Why the PnL looks like this.</b> A maker earns at most half the spread per fill:{" "}
      <b>{half.toFixed(4)} bp</b> now. Each fill pays the maker fee: <b>{fee.toFixed(2)} bp</b>
      {quoteUsd > 0 && <> (≈ ${((quoteUsd * fee) / 1e4).toFixed(2)} on one ${fmt.num(quoteUsd, 0)} quote)</>}.{" "}
      {ok
        ? "The spread covers the fee, so the edge is decided by adverse selection: whether price keeps moving after you are filled."
        : `The fee is ${Math.round(fee / Math.max(half, 1e-9)).toLocaleString()}× the spread you can capture, so every fill loses money and a bigger size only loses faster. That is why real HFT market makers trade on rebate tiers (negative maker fees) and wide-spread products. Try fees = mm_rebate.`}
    </div>
  );
}

function StrategyTable({ s }: { s: SimState }) {
  const accts = s.accounts ?? [];
  const best = Math.max(...accts.map((a) => a.equity));
  return (
    <div className="card">
      <h3>Strategies <span className="dim small">one account each · same live feed · ${fmt.num(s.capital_usd, 0)} capital</span></h3>
      <table className="grid-table">
        <thead>
          <tr>
            <th>Strategy</th><th>Net PnL</th><th>Return</th><th>Bid</th><th>Ask</th><th>Position</th>
            <th>Fills</th><th>Volume</th><th>Fees</th><th>Edge/fill</th><th>Adverse 5s</th><th>Equity</th>
          </tr>
        </thead>
        <tbody>
          {accts.map((a: SimAccount) => (
            <tr key={a.name}>
              <td><span className="dot" style={{ background: colorFor(a.name) }} />
                <span className={a.equity === best && best > 0 ? "bold" : ""}>{a.name}</span>
                {a.halted && <span className="pill neg">HALT</span>}</td>
              <td className={tone(a.equity)}>{fmt.usd(a.equity, 2)}</td>
              <td className={tone(a.return_bps)}>{fmt.signed(a.return_bps)} bp</td>
              <td><QuoteCell q={a.bid} /></td>
              <td><QuoteCell q={a.ask} /></td>
              <td className={a.position ? "info" : "dim"}>{fmt.signed(a.position, 5)}</td>
              <td>{a.fills}</td>
              <td>${fmt.num(a.volume_usd, 0)}</td>
              <td className="dim">{fmt.usd(-a.fees, 2)}</td>
              <td className={tone(a.edge_per_fill)}>{a.edge_per_fill == null ? "–" : fmt.usd(a.edge_per_fill, 4)}</td>
              <td>{a.adverse_bps == null ? "–" : `${fmt.signed(a.adverse_bps)} bp`}</td>
              <td><Sparkline values={a.equity_curve} color={colorFor(a.name)} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="dim small">Return = net PnL ÷ capital (1 bp = 0.01%). One quote is 10% of capital, inventory is capped at 100%.
        "3t back" = three ticks behind the best price; "sending" = the order is still in flight (order latency). Edge and adverse selection refresh every 5 s.</p>
    </div>
  );
}

function MarketCard({ s }: { s: SimState }) {
  const m = s.market;
  const c = s.calibration;
  const regimeClass = { QUIET: "dim", NORMAL: "pos", ELEVATED: "warn", BURST: "neg" } as const;
  return (
    <div className="card">
      <h3>Market & model</h3>
      {!m ? <p className="dim">waiting for the L2 book to sync…</p> : (
        <dl className="kv">
          <dt>Mid</dt><dd>{fmt.price(m.mid)}</dd>
          <dt>Spread</dt><dd>{m.spread.toPrecision(3)} ({m.spread_bps.toFixed(4)} bp)</dd>
          <dt>σ</dt><dd>{m.sigma.toFixed(3)} $/√s</dd>
          <dt>Hawkes λ buy / sell</dt><dd>{m.lambda_buy.toFixed(2)} / {m.lambda_sell.toFixed(2)} /s</dd>
          <dt>λ / stationary</dt><dd><span className={`pill ${regimeClass[m.regime]}`}>{m.ratio.toFixed(2)}× {m.regime}</span></dd>
          <dt>Hawkes model</dt><dd>{m.hawkes_fitted ? "MLE fit (refit every 60 s)" : "default, fitting"}</dd>
          {c && <><dt>Arrivals A, k</dt><dd>{c.A.toFixed(3)}/s, {c.k.toFixed(3)}/$</dd>
            <dt>Fair-value R²</dt><dd>{c.fair_value_r2.toFixed(3)}</dd></>}
        </dl>
      )}
    </div>
  );
}

function Fills({ s }: { s: SimState }) {
  const fills = s.fills ?? [];
  return (
    <div className="card fills">
      <h3>Fills <span className="dim small">one row per sweep: a market order prints as many tiny trades</span></h3>
      {fills.length === 0 ? <p className="dim">{s.status === "trading" ? "no fills yet" : "quotes start after warm-up"}</p> : (
        <table className="grid-table compact">
          <thead><tr><th>UTC</th><th>Strategy</th><th>Side</th><th>Price</th><th>Qty</th><th>Prints</th></tr></thead>
          <tbody>
            {fills.map((f, i) => (
              <tr key={`${f.name}-${f.ts0}-${i}`}>
                <td className="dim">{fmt.time(f.ts)}</td>
                <td><span className="dot" style={{ background: colorFor(f.name) }} />{f.name}</td>
                <td className={f.side === 1 ? "pos" : "neg"}>{f.side === 1 ? "BUY" : "SELL"}{!f.maker && " taker"}</td>
                <td>{f.px_hi - f.px_lo < 1e-9 ? fmt.price(f.px) : `${fmt.price(f.px_lo)}–${fmt.price(f.px_hi)}`}</td>
                <td>{f.qty.toFixed(5)}</td>
                <td className="dim">{f.n === 1 ? "1" : `${f.n} (one sweep)`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function LiveSim() {
  const { state, connected } = useSimState();
  const s = state;
  const statusClass = s?.status === "trading" ? "pos" : s?.status === "idle" || s?.status === "stopped" ? "dim" : "warn";
  return (
    <div className="page">
      <Controls state={s} />
      {!connected && <div className="card callout bad">Backend not reachable. Start it with <code>uvicorn main:app</code> in backend/python.</div>}
      {s && s.status !== "idle" && (
        <>
          <div className="statusbar">
            <span className={`pill ${statusClass}`}>{s.status.toUpperCase()}</span>
            <span>{s.symbol}</span>
            <span className="dim">{s.market_type}</span>
            <span className="dim">{s.fee_label}</span>
            {s.quote_qty && <span className="dim">quote {s.quote_qty} · limit {s.max_position?.toFixed(5)}</span>}
            {s.latency_ms && <span className="dim">latency {s.latency_ms[0]}+{s.latency_ms[1]} ms</span>}
            {s.recording && <span className="rec">● REC {s.recording.events.toLocaleString()} events · {s.recording.file}</span>}
          </div>
          {s.error && <div className="card callout bad">{s.error}</div>}
          {s.status !== "trading" && s.status !== "stopped" && s.warmup && (
            <div className="card">
              <h3>Warm-up <span className="dim small">nothing is quoted until σ, A, k and Hawkes are fitted on this window</span></h3>
              <div className="progress"><div style={{ width: `${Math.min(100, (100 * s.warmup.elapsed_s) / s.warmup.target_s)}%` }} /></div>
              <p className="dim small">{s.warmup.elapsed_s.toFixed(0)} / {s.warmup.target_s.toFixed(0)} s · {s.warmup.book.toLocaleString()} book updates · {s.warmup.trades.toLocaleString()} trades</p>
            </div>
          )}
          <Economics s={s} />
          {(s.accounts?.length ?? 0) > 0 && <StrategyTable s={s} />}
          <div className="row">
            {(s.accounts?.length ?? 0) > 0 && (
              <div className="card grow">
                <h3>Equity by strategy ($)</h3>
                <LineChart zeroLine series={(s.accounts ?? []).map((a) => ({ name: a.name, color: colorFor(a.name), values: a.equity_curve }))} />
                <div className="legend">{(s.accounts ?? []).map((a) => <span key={a.name}><span className="dot" style={{ background: colorFor(a.name) }} />{a.name}</span>)}</div>
              </div>
            )}
            <MarketCard s={s} />
          </div>
          <div className="row">
            <div className="card grow">
              <h3>Mid price</h3>
              <LineChart height={180} series={[{ name: "mid", color: "#e5e7eb", values: (s.mid_history ?? []).map((p) => p[1]) }]} />
            </div>
          </div>
          <Fills s={s} />
        </>
      )}
      {s?.status === "idle" && (
        <div className="card">
          <h3>Live paper trading</h3>
          <p>Every strategy trades the same live Binance feed through the backtest simulator (feed and order latency, queue position,
            post-only, fees), each with its own account. Every event is recorded to <code>data/sessions</code> and can be replayed exactly from the Sessions tab.</p>
        </div>
      )}
    </div>
  );
}
