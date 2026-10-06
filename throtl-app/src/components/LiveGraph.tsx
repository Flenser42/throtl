import { useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import { autoRate, formatBytes, formatRate } from "../lib/format";
import type { HistoryPoint } from "../lib/model";
import type { Unit } from "../lib/types";

const W = 820;
const H = 196;
const LEFT = 80;   // gutter wide enough for the widest y label ("999.9 MB/s")
const TOP = 24;
const BOTTOM = 168;

const WINDOWS: { id: string; label: string; seconds: number }[] = [
  { id: "30s", label: "30s", seconds: 30 },
  { id: "1m", label: "1m", seconds: 60 },
  { id: "5m", label: "5m", seconds: 300 },
  { id: "15m", label: "15m", seconds: 900 },
  { id: "all", label: "all", seconds: Infinity },
];

interface Props {
  history: HistoryPoint[];
  matchedApps: number;
  peakApp: string;
  unit: Unit;
}

export function LiveGraph({ history, matchedApps, peakApp, unit }: Props) {
  const [win, setWin] = useState("1m");
  const [hover, setHover] = useState<number | null>(null);
  // `?pin=N` pins a sample for deterministic screenshots; otherwise the
  // crosshair only appears under the cursor / after a click.
  const [pinned, setPinned] = useState<number | null>(() => {
    const raw = new URLSearchParams(location.search).get("pin");
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) ? n : null;
  });
  const wrapRef = useRef<HTMLDivElement>(null);

  const data = useMemo(() => {
    const seconds = WINDOWS.find((w) => w.id === win)?.seconds ?? 60;
    if (!Number.isFinite(seconds)) return history;
    // +1 so the window includes its left boundary sample (1 Hz data).
    return history.slice(-Math.max(2, seconds + 1));
  }, [history, win]);

  const metrics = useMemo(() => {
    const downs = data.map((d) => d.down);
    const max = Math.max(1, ...data.map((d) => Math.max(d.down, d.up)));
    const yMax = max;
    const min = downs.length ? Math.min(...downs) : 0;
    const avg = downs.length ? downs.reduce((a, b) => a + b, 0) / downs.length : 0;
    return { yMax, min, avg, max };
  }, [data]);

  // Integral of the 1 Hz samples over the *shown* window (kbit/s -> bytes).
  const windowSumBytes = useMemo(
    () => (data.reduce((sum, p) => sum + p.down + p.up, 0) * 1000) / 8,
    [data],
  );

  const x = (i: number) =>
    data.length < 2 ? LEFT : LEFT + (i / (data.length - 1)) * (W - LEFT);
  const y = (v: number) => BOTTOM - (Math.min(v, metrics.yMax) / metrics.yMax) * (BOTTOM - TOP);

  /**
   * Fritsch-Carlson monotone cubic interpolation: the curve reads as one smooth
   * signal instead of a polyline, but it can never overshoot the measured
   * values (a plain Catmull-Rom would invent peaks between samples).
   */
  const smoothPath = (values: number[]) => {
    const n = values.length;
    if (n === 0) return "";
    if (n === 1) return `M${x(0).toFixed(1)},${y(values[0]).toFixed(1)}`;
    const dx: number[] = [];
    const delta: number[] = [];
    for (let i = 0; i < n - 1; i += 1) {
      dx[i] = x(i + 1) - x(i);
      delta[i] = dx[i] === 0 ? 0 : (values[i + 1] - values[i]) / dx[i];
    }
    const m: number[] = new Array(n);
    m[0] = delta[0];
    m[n - 1] = delta[n - 2];
    for (let i = 1; i < n - 1; i += 1) {
      m[i] = delta[i - 1] * delta[i] <= 0 ? 0 : (delta[i - 1] + delta[i]) / 2;
    }
    for (let i = 0; i < n - 1; i += 1) {
      if (delta[i] === 0) {
        m[i] = 0;
        m[i + 1] = 0;
        continue;
      }
      const a = m[i] / delta[i];
      const b = m[i + 1] / delta[i];
      const s = a * a + b * b;
      if (s > 9) {
        const t = 3 / Math.sqrt(s);
        m[i] = t * a * delta[i];
        m[i + 1] = t * b * delta[i];
      }
    }
    let d = `M${x(0).toFixed(1)},${y(values[0]).toFixed(1)}`;
    for (let i = 0; i < n - 1; i += 1) {
      const x1 = x(i + 1);
      const c1x = x(i) + dx[i] / 3;
      const c1y = y(values[i] + (m[i] * dx[i]) / 3);
      const c2x = x1 - dx[i] / 3;
      const c2y = y(values[i + 1] - (m[i + 1] * dx[i]) / 3);
      d +=
        ` C${c1x.toFixed(1)},${c1y.toFixed(1)}` +
        ` ${c2x.toFixed(1)},${c2y.toFixed(1)}` +
        ` ${x1.toFixed(1)},${y(values[i + 1]).toFixed(1)}`;
    }
    return d;
  };

  const downLine = smoothPath(data.map((d) => d.down));
  const upLine = smoothPath(data.map((d) => d.up));
  // Only a real area needs a closed path; with 0 or 1 samples the empty string
  // would be parsed as "L W,H …" and SVG would fill a triangle from the origin.
  const downArea =
    data.length > 1 ? `${downLine} L${W},${H} L${LEFT},${H} Z` : "";

  // A pinned index can point outside the current window (e.g. pinned on "all",
  // then switched to "30s"); clamp instead of silently showing nothing.
  const active =
    hover ?? (pinned == null ? null : Math.max(0, Math.min(data.length - 1, pinned)));
  const activePoint = active != null ? data[active] : undefined;
  const secondsAgo = active != null ? data.length - 1 - active : 0;

  const onMove = (event: ReactMouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || data.length < 2 || rect.width === 0) return;
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    setHover(Math.round(ratio * (data.length - 1)));
  };

  const yLabel = (v: number) => {
    const { value, unit: label } = autoRate(v, 1);
    return `${value} ${label}`;
  };

  return (
    <div className="graph-section" data-tour="graph">
      <div className="card-head">
        <div className="card-title">Live traffic</div>
        <div className="card-sub">
          Σ {formatBytes(windowSumBytes, 2, true)} in this window · {matchedApps} apps matched
        </div>
        <div className="seg" style={{ marginLeft: "auto" }}>
          {WINDOWS.map((w) => (
            <button
              key={w.id}
              type="button"
              className={win === w.id ? "on" : ""}
              onClick={() => setWin(w.id)}
            >
              {w.label}
            </button>
          ))}
        </div>
        <div className="legend">
          <span>
            <i className="swatch" style={{ background: "var(--graph-down)" }} />
            Download
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--graph-up)" }} />
            Upload
          </span>
        </div>
      </div>

      <div
        className="graph-wrap"
        ref={wrapRef}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        onClick={() => setPinned((prev) => (prev === hover ? null : hover))}
      >
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={
            `Live traffic chart. Download: minimum ${formatRate(metrics.min, unit, 1)}, ` +
            `average ${formatRate(metrics.avg, unit, 1)}, ` +
            `maximum ${formatRate(metrics.max, unit, 1)}. Peak app: ${peakApp}.`
          }
        >
          {/* Graticule: a ruled measuring field, verticals and horizontals. */}
          <g stroke="var(--rule)" strokeWidth="1">
            <line x1={LEFT} y1="24" x2={W} y2="24" />
            <line x1={LEFT} y1="72" x2={W} y2="72" />
            <line x1={LEFT} y1="120" x2={W} y2="120" />
            {[0.25, 0.5, 0.75].map((f) => (
              <line
                key={f}
                x1={LEFT + (W - LEFT) * f}
                y1="24"
                x2={LEFT + (W - LEFT) * f}
                y2="168"
                opacity=".18"
              />
            ))}
          </g>
          <line x1={LEFT} y1="168" x2={W} y2="168" stroke="var(--rule-strong)" strokeWidth="1" />

          {downArea && <path d={downArea} fill="var(--down-wash)" />}
          {downLine && (
            <path
              d={downLine}
              fill="none"
              stroke="var(--graph-down)"
              strokeWidth="1.5"
              strokeLinejoin="miter"
              strokeLinecap="butt"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {upLine && (
            <path
              d={upLine}
              fill="none"
              stroke="var(--graph-up)"
              strokeWidth="1.5"
              strokeLinejoin="miter"
              vectorEffect="non-scaling-stroke"
            />
          )}

          {activePoint && active != null && (
            <>
              <line
                x1={x(active)}
                y1="24"
                x2={x(active)}
                y2="168"
                stroke="var(--text-3)"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
              <rect
                x={x(active) - 3}
                y={y(activePoint.down) - 3}
                width="6"
                height="6"
                fill="var(--down)"
                stroke="var(--surface)"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
              <rect
                x={x(active) - 3}
                y={y(activePoint.up) - 3}
                width="6"
                height="6"
                fill="var(--up)"
                stroke="var(--surface)"
                strokeWidth="1"
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}

          <g fill="var(--text-3)" fontFamily="var(--mono)" fontSize="10" textAnchor="end">
            <text x={LEFT - 8} y="20">
              {yLabel(metrics.yMax)}
            </text>
            <text x={LEFT - 8} y="68">
              {yLabel(metrics.yMax * (2 / 3))}
            </text>
            <text x={LEFT - 8} y="116">
              {yLabel(metrics.yMax * (1 / 3))}
            </text>
            <text x={LEFT - 8} y="164">
              0
            </text>
          </g>
          <g fill="var(--text-3)" fontFamily="var(--mono)" fontSize="10">
            <text x={W - 4} y="182" textAnchor="end">
              now
            </text>
            <text x={(LEFT + W) / 2} y="182" textAnchor="middle">
              -{Math.round((data.length - 1) / 2)}s
            </text>
            {data.length > 1 && (
              <text x={LEFT} y="182">
                -{data.length - 1}s
              </text>
            )}
          </g>
        </svg>

        {activePoint && active != null && (
          <div
            className="tooltip"
            style={{ left: `${Math.max(0.11, Math.min(0.89, x(active) / W)) * 100}%` }}
          >
            <div className="tt-time mono">-{secondsAgo}s · {clockLabel(secondsAgo)}</div>
            <div className="tt-row">
              <i className="swatch" style={{ background: "var(--graph-down)" }} />
              Download
              <b className="t-down">{formatRate(activePoint.down, unit, 2)}</b>
            </div>
            <div className="tt-row" style={{ marginTop: 4 }}>
              <i className="swatch" style={{ background: "var(--graph-up)" }} />
              Upload
              <b className="t-up">{formatRate(activePoint.up, unit, 2)}</b>
            </div>
          </div>
        )}
      </div>

      <div className="graph-stats">
        <span>
          min <b>{formatRate(metrics.min, unit, 1)}</b>
        </span>
        <span>
          avg <b>{formatRate(metrics.avg, unit, 1)}</b>
        </span>
        <span>
          max <b>{formatRate(metrics.max, unit, 1)}</b>
        </span>
        <span>
          peak app <b>{peakApp}</b>
        </span>
      </div>
    </div>
  );
}

function clockLabel(secondsAgo: number): string {
  const d = new Date(Date.now() - secondsAgo * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
