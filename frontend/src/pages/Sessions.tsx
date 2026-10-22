import { useEffect, useState } from "react";
import { colorFor, fmt, tone, useFetch } from "../api";
import { LineChart } from "../charts";

interface SessionInfo { file: string; bytes: number; symbol: string; fee: string; capital_usd: number; started_utc: string }
interface ReplayAccount {
  name: string; net_pnl: number; fees: number; fills: number; position: number; return_bps: number | null;
  equity_curve: number[]; recorded: number; replayed: number; match: boolean; first_mismatch: number | null;
}
interface ReplayResult { file: string; fee: string; events: number; by_kind: Record<string, number>; meta: Record<string, unknown>; accounts: ReplayAccount[] }

const FEES = ["", "spot_vip0", "vip0", "vip9", "mm_rebate", "zero"];

export default function Sessions() {
  const { data: sessions, error } = useFetch<SessionInfo[]>("/api/v1/sessions");
  const [file, setFile] = useState<string | null>(null);
  const [fee, setFee] = useState("");
  useEffect(() => {
    if (!file && sessions?.length) setFile(sessions[0].file);
  }, [sessions, file]);
  const url = file ? `/api/v1/sessions/${encodeURIComponent(file)}/replay${fee ? `?fee=${fee}` : ""}` : null;
  const { data: rep, error: repErr, loading } = useFetch<ReplayResult>(url);
  const asRecorded = !fee || fee === rep?.meta?.fee;
  return (
    <div className="page">
      <div className="card">
        <h3>Recorded sessions <span className="dim small">every live session writes each book update, trade, calibration, Hawkes refit, order, cancel and fill in order to data/sessions</span></h3>
        {error && <p className="neg">{error}</p>}
        {sessions?.length === 0 && <p className="dim">No sessions yet. Start one on the Live sim tab.</p>}
        {sessions && sessions.length > 0 && (
          <table className="grid-table compact clickable">
            <thead><tr><th>File</th><th>Symbol</th><th>Fees</th><th>Capital</th><th>Started (UTC)</th><th>Size</th></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.file} className={s.file === file ? "selected" : ""} onClick={() => setFile(s.file)}>
                  <td>{s.file}</td><td>{s.symbol}</td><td>{s.fee}</td><td>${fmt.num(s.capital_usd, 0)}</td>
                  <td className="dim">{s.started_utc}</td><td className="dim">{(s.bytes / 1024).toFixed(0)} KB</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {file && (
        <div className="card controls">
          <span>Replay <b>{file}</b></span>
          <label>under fees
            <select value={fee} onChange={(e) => setFee(e.target.value)}>
              {FEES.map((f) => <option key={f} value={f}>{f || "as recorded"}</option>)}
            </select>
          </label>
          {loading && <span className="dim">replaying…</span>}
          {repErr && <span className="neg">{repErr}</span>}
        </div>
      )}
      {rep && (
        <>
          <div className="statusbar">
            <span>{rep.events.toLocaleString()} events</span>
            {Object.entries(rep.by_kind).map(([k, v]) => <span key={k} className="dim">{k} {v.toLocaleString()}</span>)}
          </div>
          <div className="card">
            <h3>{asRecorded ? "Recorded vs replayed" : `Same market data, replayed at ${rep.fee}`}
              <span className="dim small">{asRecorded ? " the simulator is deterministic, so every fill must match exactly" : " fills can differ: fee-aware strategies quote differently"}</span></h3>
            <table className="grid-table">
              <thead><tr><th>Strategy</th><th>Net PnL</th><th>Return</th><th>Fees</th><th>Fills recorded</th><th>Fills replayed</th><th>Position</th><th>{asRecorded ? "Match" : "Same fills?"}</th></tr></thead>
              <tbody>
                {rep.accounts.map((a) => (
                  <tr key={a.name}>
                    <td><span className="dot" style={{ background: colorFor(a.name) }} />{a.name}</td>
                    <td className={tone(a.net_pnl)}>{fmt.usd(a.net_pnl)}</td>
                    <td className={tone(a.return_bps)}>{fmt.signed(a.return_bps)} bp</td>
                    <td className="dim">{fmt.usd(-a.fees)}</td>
                    <td>{a.recorded}</td><td>{a.replayed}</td>
                    <td>{fmt.signed(a.position, 5)}</td>
                    <td className={a.match ? "pos" : asRecorded ? "neg" : "dim"}>{a.match ? "exact" : `differs at #${a.first_mismatch}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="card">
            <h3>Equity by strategy ($)</h3>
            <LineChart zeroLine series={rep.accounts.map((a) => ({ name: a.name, color: colorFor(a.name), values: a.equity_curve }))} />
            <div className="legend">{rep.accounts.map((a) => <span key={a.name}><span className="dot" style={{ background: colorFor(a.name) }} />{a.name}</span>)}</div>
          </div>
        </>
      )}
    </div>
  );
}
