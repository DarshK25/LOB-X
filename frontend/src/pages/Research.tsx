import { Fragment, useEffect, useState } from "react";
import { colorFor, fmt, tone, useFetch } from "../api";
import { BarChartCI, LineChart } from "../charts";

interface WindowInfo { id: string; window: string; venue: string; hours: number; oos_hours: number; fees: string[]; cross: boolean }
type Row = Record<string, number | string | boolean | null>;
interface FeeBlock {
  profile: { maker: number; taker: number; label: string };
  tuning: { AS: number; GLFT: number; grid?: { gamma: number; AS: Row; GLFT: Row }[] };
  oos: Row[];
  stress: Row[];
  bootstrap: Record<string, { pnl: number; pnl_ci: [number, number]; p_positive: number; diff_ci?: [number, number]; p_better_than_baseline?: number }>;
}
interface Result {
  id: string; window: string; venue: string; hours: number; oos_hours: number; oos_sigma?: number;
  calibration: {
    sigma: number; sigma_bps_per_sqrt_s: number;
    arrival: { A: number; k: number; r2: number; depths: number[]; rates: number[]; market_orders: number };
    hawkes: Record<"buy" | "sell", { mu: number; alpha: number; beta: number; branching_ratio: number; events: number }>;
    fair_value: { r2_in_sample: number; r2_out_of_sample?: number; horizon_ms: number };
  };
  fees: Record<string, FeeBlock>;
}

const num = (r: Row, k: string) => (typeof r[k] === "number" ? (r[k] as number) : null);

function Calibration({ res }: { res: Result }) {
  const c = res.calibration;
  const a = c.arrival;
  const depths = a.depths.slice(1);
  const rates = a.rates.slice(1);
  const fitted = depths.map((d) => a.A * Math.exp(-a.k * d));
  return (
    <div className="row">
      <div className="card">
        <h3>Calibration <span className="dim small">in-sample half</span></h3>
        <dl className="kv">
          <dt>σ</dt><dd>{c.sigma.toFixed(3)} $/√s ({c.sigma_bps_per_sqrt_s.toFixed(3)} bp/√s)</dd>
          <dt>Arrivals λ(δ) = A e^(−kδ)</dt><dd>A {a.A.toFixed(3)}/s · k {a.k.toFixed(3)}/$ · R² {a.r2.toFixed(2)}</dd>
          <dt>Market orders used</dt><dd>{a.market_orders.toLocaleString()}</dd>
          {(["buy", "sell"] as const).map((s) => (
            <Fragment key={s}><dt>Hawkes {s}</dt><dd>μ {c.hawkes[s].mu.toFixed(3)} · α {c.hawkes[s].alpha.toFixed(3)} · β {c.hawkes[s].beta.toFixed(3)} · n = {c.hawkes[s].branching_ratio.toFixed(2)}</dd></Fragment>
          ))}
          <dt>Fair-value R²</dt><dd>in {c.fair_value.r2_in_sample.toFixed(3)} · out {c.fair_value.r2_out_of_sample?.toFixed(3) ?? "–"} ({c.fair_value.horizon_ms} ms ahead)</dd>
        </dl>
      </div>
      <div className="card grow">
        <h3>Fill intensity vs quote depth <span className="dim small">market orders reaching δ $ from mid per second: empirical (white) vs fitted exponential (violet) · at the touch: {a.rates[0].toFixed(2)}/s, not part of the fit</span></h3>
        <LineChart height={200} markers series={[
          { name: "empirical", color: "#e5e7eb", values: rates, x: depths },
          { name: "fit", color: "#a78bfa", values: fitted, x: depths },
        ]} xLabel={(x) => (Math.abs(x * 10 - Math.round(x * 10)) < 1e-6 && Math.round(x * 10) % 5 === 0 ? `$${x.toFixed(1)}` : "")} yLabel="per s" />
      </div>
    </div>
  );
}

function GammaGrid({ block }: { block: FeeBlock }) {
  const grid = block.tuning.grid;
  if (!grid?.length) return null;
  const xs = grid.map((g) => g.gamma);
  return (
    <div className="card grow">
      <h3>γ tuning <span className="dim small">in-sample net PnL vs risk aversion γ (log scale, 1/$) · chosen: AS {block.tuning.AS}, GLFT {block.tuning.GLFT} =
        best in-sample PnL among settings that still fill ≥ 100 times per hour (a γ that never quotes "wins" with $0)</span></h3>
      <LineChart height={200} logX zeroLine markers xLabel={(x) => `${x}`} series={[
        { name: "AS", color: colorFor("AS"), values: grid.map((g) => num(g.AS, "net_pnl") ?? 0), x: xs },
        { name: "GLFT", color: colorFor("GLFT"), values: grid.map((g) => num(g.GLFT, "net_pnl") ?? 0), x: xs },
      ]} />
      <div className="legend"><span><span className="dot" style={{ background: colorFor("AS") }} />AS</span><span><span className="dot" style={{ background: colorFor("GLFT") }} />GLFT</span></div>
    </div>
  );
}

function OOS({ block, hours }: { block: FeeBlock; hours: number }) {
  const bars = block.oos.map((r) => {
    const name = String(r.strategy);
    const b = block.bootstrap[name];
    return { name, value: num(r, "net_pnl") ?? 0, lo: b?.pnl_ci?.[0], hi: b?.pnl_ci?.[1], color: colorFor(name) };
  });
  return (
    <>
      <div className="card">
        <h3>Out-of-sample net PnL <span className="dim small">{hours.toFixed(1)} h never seen during calibration or tuning · whiskers: 95% moving-block bootstrap CI</span></h3>
        <BarChartCI bars={bars} />
      </div>
      <div className="card">
        <h3>Out-of-sample metrics</h3>
        <table className="grid-table">
          <thead><tr><th>Strategy</th><th>Net $</th><th>95% CI</th><th>P(&gt;0)</th><th>Fills</th><th>Edge/fill $</th><th>Adverse 5s bp</th><th>Fees $</th><th>Sharpe</th><th>Max DD $</th><th>Break-even fee bp</th></tr></thead>
          <tbody>
            {block.oos.map((r) => {
              const name = String(r.strategy);
              const b = block.bootstrap[name];
              return (
                <tr key={name}>
                  <td><span className="dot" style={{ background: colorFor(name) }} />{name}</td>
                  <td className={tone(num(r, "net_pnl"))}>{fmt.signed(num(r, "net_pnl"))}</td>
                  <td className="dim">{b ? `[${fmt.signed(b.pnl_ci[0])}, ${fmt.signed(b.pnl_ci[1])}]` : "–"}</td>
                  <td>{b ? `${(b.p_positive * 100).toFixed(0)}%` : "–"}</td>
                  <td>{num(r, "fills")}</td>
                  <td className={tone(num(r, "edge_per_fill_usd"))}>{fmt.signed(num(r, "edge_per_fill_usd"), 5)}</td>
                  <td>{fmt.signed(num(r, "adverse_bps"))}</td>
                  <td className="dim">{fmt.num(num(r, "fees"))}</td>
                  <td>{fmt.signed(num(r, "sharpe"), 1)}</td>
                  <td>{fmt.num(num(r, "max_drawdown"))}</td>
                  <td>{fmt.signed(num(r, "breakeven_maker_fee_bps"))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="dim small">Break-even fee: the maker fee (bp) at which the strategy would net zero. Negative means it needs a rebate.</p>
      </div>
      {block.stress.length > 0 && (
        <div className="card">
          <h3>Stress tests <span className="dim small">best model re-run with harsher assumptions</span></h3>
          <table className="grid-table">
            <thead><tr><th>Scenario</th><th>Net $</th><th>Fills</th><th>Adverse 5s bp</th><th>Sharpe</th></tr></thead>
            <tbody>
              {block.stress.map((r) => (
                <tr key={String(r.strategy)}>
                  <td>{String(r.strategy).replace(/^stress: /, "")}</td>
                  <td className={tone(num(r, "net_pnl"))}>{fmt.signed(num(r, "net_pnl"))}</td>
                  <td>{num(r, "fills")}</td>
                  <td>{fmt.signed(num(r, "adverse_bps"))}</td>
                  <td>{fmt.signed(num(r, "sharpe"), 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export default function Research() {
  const { data: windows, error } = useFetch<WindowInfo[]>("/api/v1/research");
  const [rid, setRid] = useState<string | null>(null);
  const [fee, setFee] = useState<string | null>(null);
  useEffect(() => {
    if (!rid && windows?.length) setRid(windows[0].id);
  }, [windows, rid]);
  const { data: res, loading } = useFetch<Result>(rid ? `/api/v1/research/${rid}` : null);
  const fees = res ? Object.keys(res.fees) : [];
  const activeFee = fee && fees.includes(fee) ? fee : fees[0];
  const block = res && activeFee ? res.fees[activeFee] : null;
  return (
    <div className="page">
      <div className="card controls">
        <label>Window
          <select value={rid ?? ""} onChange={(e) => setRid(e.target.value)}>
            {(windows ?? []).map((w) => <option key={w.id} value={w.id}>{w.cross ? "cross-regime: " : ""}{w.window}</option>)}
          </select>
        </label>
        <label>Fee scenario
          <select value={activeFee ?? ""} onChange={(e) => setFee(e.target.value)}>
            {fees.map((f) => <option key={f} value={f}>{res!.fees[f].profile.label}</option>)}
          </select>
        </label>
        {loading && <span className="dim">loading…</span>}
        {error && <span className="neg">{error}</span>}
      </div>
      {windows?.length === 0 && <div className="card">No results. Run <code>python scripts/run_research.py</code>.</div>}
      {res && block && (
        <>
          <div className="statusbar">
            <span>{res.window}</span><span className="dim">{res.venue}</span>
            <span className="dim">{res.hours.toFixed(1)} h · 50/50 in-sample / out-of-sample</span>
            <span className="dim">{block.profile.label}</span>
          </div>
          <Calibration res={res} />
          <div className="row"><GammaGrid block={block} /></div>
          <OOS block={block} hours={res.oos_hours} />
        </>
      )}
    </div>
  );
}
