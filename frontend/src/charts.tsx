import { useMemo } from "react";

export interface Series {
  name: string;
  color: string;
  values: number[];
  x?: number[];
}

function niceTicks(lo: number, hi: number, n = 4): number[] {
  if (!isFinite(lo) || !isFinite(hi)) return [];
  if (hi - lo < 1e-12) return [lo];
  const raw = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toPrecision(12));
  return out;
}

/** Axis label with just enough decimals to tell neighbouring gridlines apart. */
const axisLabel = (v: number, step: number) => {
  const nd = Math.max(0, Math.min(8, -Math.floor(Math.log10(step || 1))));
  return v.toLocaleString(undefined, { minimumFractionDigits: nd, maximumFractionDigits: nd });
};

interface LineChartProps {
  series: Series[];
  height?: number;
  zeroLine?: boolean;
  logX?: boolean;
  xLabel?: (x: number) => string;
  yLabel?: string;
  markers?: boolean;
}

/** Multi-series line chart. Series without ``x`` are spread evenly over the width. */
export function LineChart({ series, height = 220, zeroLine = false, logX = false, xLabel, yLabel, markers }: LineChartProps) {
  const W = 800;
  const H = height;
  const pad = { l: 72, r: 12, t: 10, b: 24 };
  const geo = useMemo(() => {
    const pts = series.filter((s) => s.values.length > 0);
    const ys = pts.flatMap((s) => s.values).filter((v) => isFinite(v));
    if (zeroLine) ys.push(0);
    let lo = Math.min(...ys);
    let hi = Math.max(...ys);
    if (!isFinite(lo)) return null;
    if (hi - lo < 1e-9) {
      lo -= 1;
      hi += 1;
    }
    const m = (hi - lo) * 0.08;
    lo -= m;
    hi += m;
    const tx = (v: number) => (logX ? Math.log10(v) : v);
    const xs = pts.flatMap((s) => (s.x ? s.x.map(tx) : [0, s.values.length - 1]));
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs) === x0 ? x0 + 1 : Math.max(...xs);
    const X = (v: number) => pad.l + ((v - x0) / (x1 - x0)) * (W - pad.l - pad.r);
    const Y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const paths = pts.map((s) => {
      const coords = s.values.map((v, i) => [X(s.x ? tx(s.x[i]) : i), Y(v)] as const);
      return { s, coords, d: coords.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("") };
    });
    const xticks = pts[0]?.x ? pts[0].x : [];
    return { paths, Y, X, lo, hi, tx, xticks };
  }, [series, H, zeroLine, logX]);

  if (!geo) return <div className="empty" style={{ height }}>no data yet</div>;
  const yt = niceTicks(geo.lo, geo.hi);
  const step = yt.length > 1 ? yt[1] - yt[0] : 1;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" preserveAspectRatio="none" style={{ height }}>
      {yt.map((v) => (
        <g key={v}>
          <line x1={pad.l} x2={W - pad.r} y1={geo.Y(v)} y2={geo.Y(v)} className="grid" />
          <text x={pad.l - 6} y={geo.Y(v) + 4} textAnchor="end" className="axis">{axisLabel(v, step)}</text>
        </g>
      ))}
      {zeroLine && <line x1={pad.l} x2={W - pad.r} y1={geo.Y(0)} y2={geo.Y(0)} className="zero" />}
      {xLabel && geo.xticks.map((x) => (
        <text key={x} x={geo.X(geo.tx(x))} y={H - 6} textAnchor="middle" className="axis">{xLabel(x)}</text>
      ))}
      {yLabel && <text x={4} y={pad.t + 8} className="axis">{yLabel}</text>}
      {geo.paths.map(({ s, d, coords }) => (
        <g key={s.name}>
          <path d={d} fill="none" stroke={s.color} strokeWidth={1.8} vectorEffect="non-scaling-stroke" />
          {markers && coords.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={3} fill={s.color} />)}
        </g>
      ))}
    </svg>
  );
}

export function Sparkline({ values, color, width = 120, height = 28 }: { values: number[]; color: string; width?: number; height?: number }) {
  if (values.length < 2) return <svg width={width} height={height} />;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const d = values
    .map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1)}`)
    .join("");
  return (
    <svg width={width} height={height}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

export interface Bar {
  name: string;
  value: number;
  lo?: number | null;
  hi?: number | null;
  color: string;
}

/** Horizontal bars around zero with optional confidence-interval whiskers. */
export function BarChartCI({ bars, unit = "$" }: { bars: Bar[]; unit?: string }) {
  const W = 800;
  const row = 30;
  const pad = { l: 160, r: 70, t: 8 };
  const H = pad.t * 2 + bars.length * row;
  const vals = bars.flatMap((b) => [b.value, b.lo ?? b.value, b.hi ?? b.value, 0]);
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const m = (hi - lo || 1) * 0.06;
  lo -= m;
  hi += m;
  const X = (v: number) => pad.l + ((v - lo) / (hi - lo)) * (W - pad.l - pad.r);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" style={{ height: H }}>
      <line x1={X(0)} x2={X(0)} y1={0} y2={H} className="zero" />
      {bars.map((b, i) => {
        const y = pad.t + i * row;
        const x0 = X(Math.min(0, b.value));
        const w = Math.abs(X(b.value) - X(0));
        return (
          <g key={b.name}>
            <text x={pad.l - 8} y={y + row / 2 + 4} textAnchor="end" className="label">{b.name}</text>
            <rect x={x0} y={y + 6} width={Math.max(w, 1)} height={row - 12} fill={b.color} opacity={0.85} rx={2} />
            {b.lo != null && b.hi != null && (
              <g stroke="#e5e7eb" strokeWidth={1.2}>
                <line x1={X(b.lo)} x2={X(b.hi)} y1={y + row / 2} y2={y + row / 2} />
                <line x1={X(b.lo)} x2={X(b.lo)} y1={y + 9} y2={y + row - 9} />
                <line x1={X(b.hi)} x2={X(b.hi)} y1={y + 9} y2={y + row - 9} />
              </g>
            )}
            <text x={W - pad.r + 6} y={y + row / 2 + 4} className={`label ${b.value > 0 ? "pos" : b.value < 0 ? "neg" : "dim"}`}>
              {b.value > 0 ? "+" : ""}{unit === "$" ? b.value.toFixed(2) : b.value.toFixed(2) + unit}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
