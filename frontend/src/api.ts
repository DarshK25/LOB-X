import { useEffect, useRef, useState } from "react";

export interface Quote {
  px: number;
  qty: number;
  ticks_behind: number;
  sending: boolean;
  queue_ahead: number | null;
}

export interface SimAccount {
  name: string;
  equity: number;
  return_bps: number;
  position: number;
  fills: number;
  filled_qty: number;
  volume_usd: number;
  fees: number;
  orders: number;
  halted: boolean;
  bid: Quote | null;
  ask: Quote | null;
  equity_curve: number[];
  edge_per_fill: number | null;
  adverse_bps: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
}

export interface FillGroup {
  name: string;
  ts0: number;
  ts: number;
  side: 1 | -1;
  px: number;
  px_lo: number;
  px_hi: number;
  qty: number;
  n: number;
  maker: boolean;
}

export interface Market {
  ts: number;
  bid: number;
  ask: number;
  mid: number;
  spread: number;
  spread_bps: number;
  sigma: number;
  lambda_buy: number;
  lambda_sell: number;
  ratio: number;
  regime: "QUIET" | "NORMAL" | "ELEVATED" | "BURST";
  hawkes_fitted: boolean;
}

export interface SimState {
  status: "idle" | "warming up" | "calibrating" | "trading" | "stopped";
  fees?: string[];
  error?: string | null;
  symbol?: string;
  fee?: string;
  fee_label?: string;
  maker_fee_bps?: number;
  taker_fee_bps?: number;
  capital_usd?: number;
  quote_qty?: number | null;
  max_position?: number | null;
  tick_size?: number;
  latency_ms?: [number, number];
  started_at?: number;
  warmup?: { elapsed_s: number; target_s: number; book: number; trades: number };
  recording?: { file: string; events: number } | null;
  market?: Market | null;
  mid_history?: [number, number][];
  calibration?: { sigma: number; A: number; k: number; fair_value_r2: number } | null;
  accounts?: SimAccount[];
  fills?: FillGroup[];
}

export interface FeeProfile {
  maker: number;
  taker: number;
  label: string;
}

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status} ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`);
  return r.json();
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail ?? r.statusText));
  }
  return r.json();
}

/** Live sim state over /ws/sim, reconnecting if the backend restarts. */
export function useSimState(): { state: SimState | null; connected: boolean } {
  const [state, setState] = useState<SimState | null>(null);
  const [connected, setConnected] = useState(false);
  const retry = useRef<number>();
  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/sim`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => setState(JSON.parse(e.data));
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry.current = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry.current);
      ws?.close();
    };
  }, []);
  return { state, connected };
}

export function useFetch<T>(path: string | null): { data: T | null; error: string | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setLoading(true);
    setError(null);
    getJSON<T>(path)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(String(e.message ?? e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [path]);
  return { data, error, loading };
}

export const STRATEGY_COLORS: Record<string, string> = {
  NoTrade: "#6b7280",
  FixedSpread: "#f59e0b",
  InventorySkew: "#fb7185",
  AS: "#38bdf8",
  GLFT: "#a78bfa",
  "GLFT+Hawkes+FV": "#34d399",
  "GLFT+HFV fee-aware": "#facc15",
};

export const colorFor = (name: string) => STRATEGY_COLORS[name] ?? "#94a3b8";

export const fmt = {
  usd: (x: number | null | undefined, nd = 2) =>
    x == null ? "–" : `${x < 0 ? "−" : x > 0 ? "+" : ""}$${Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: nd, maximumFractionDigits: nd })}`,
  num: (x: number | null | undefined, nd = 2) =>
    x == null ? "–" : x.toLocaleString(undefined, { minimumFractionDigits: nd, maximumFractionDigits: nd }),
  signed: (x: number | null | undefined, nd = 2) => (x == null ? "–" : `${x > 0 ? "+" : ""}${x.toFixed(nd)}`),
  price: (x: number | null | undefined) =>
    x == null ? "–" : x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  time: (ms: number) => new Date(ms).toISOString().slice(11, 19),
};

export const tone = (x: number | null | undefined) => (x == null || x === 0 ? "dim" : x > 0 ? "pos" : "neg");
