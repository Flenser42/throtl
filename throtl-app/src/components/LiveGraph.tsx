import { useId, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import { autoRate, formatBytes, formatRate } from "../lib/format";
import type { HistoryPoint } from "../lib/model";

const W = 820;
const H = 196;
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
  windowSumBytes: number;
  peakApp: string;
}

export function LiveGraph({ history, matchedApps, windowSumBytes, peakApp }: Props) {
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
  const areaId = useId();

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

  const x = (i: number) => (data.length < 2 ? 0 : (i / (data.length - 1)) * W);
  const y = (v: number) => BOTTOM - (Math.min(v, metrics.yMax) / metrics.yMax) * (BOTTOM - TOP);

  const line = (key: "down" | "up") =>
    data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d[key]).toFixed(1)}`).join(" ");
  const downLine = line("down");
  const upLine = line("up");
  const downArea = `${downLine} L${W},${H} L0,${H} Z`;

  const active = hover ?? pinned;
  const activePoint = active != null ? data[active] : undefined;
  const secondsAgo = active != null ? data.length - 1 - active : 0;

  const onMove = (event: ReactMouseEvent) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect || data.length < 2) return;
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    setHover(Math.round(ratio * (data.length - 1)));
  };

  const yLabel = (v: number) => {
    const { value, unit } = autoRate(v, 1);
    return `${value} ${unit}`;
  };

  return (
    <div className="graph-section">
      <div className="card-head">
        <div>
          <div className="card-title">Live traffic</div>
          <div className="card-sub">
            Σ {formatBytes(windowSumBytes, 2, true)} in this window · {matchedApps} apps matched
          </div>
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
            <i className="swatch" style={{ background: "var(--down)" }} />
            Download
          </span>
          <span>
            <i className="swatch" style={{ background: "var(--up)" }} />
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
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          <defs>
            <linearGradient id={areaId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="var(--down)" stopOpacity=".30" />
              <stop offset="1" stopColor="var(--down)" stopOpacity=".02" />
            </linearGradient>
          </defs>

          <g stroke="var(--hairline)" strokeWidth="1" strokeDasharray="2 5" opacity=".8">
            <line x1="0" y1="24" x2={W} y2="24" />
            <line x1="0" y1="72" x2={W} y2="72" />
            <line x1="0" y1="120" x2={W} y2="120" />
          </g>
          <line x1="0" y1="168" x2={W} y2="168" stroke="var(--border)" strokeWidth="1" />

          <path d={downArea} fill={`url(#${areaId})`} />
          <path
            d={downLine}
            fill="none"
            stroke="var(--down)"
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={upLine}
            fill="none"
            stroke="var(--up)"
            strokeWidth="1.7"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />

          {activePoint && active != null && (
            <>
              <line
                x1={x(active)}
                y1="0"
                x2={x(active)}
                y2={H}
                stroke="var(--accent)"
                strokeWidth="1"
                strokeDasharray="3 3"
                opacity=".85"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={x(active)}
                cy={y(activePoint.down)}
                r="4.5"
                fill="var(--bg)"
                stroke="var(--down)"
                strokeWidth="2"
              />
              <circle
                cx={x(active)}
                cy={y(activePoint.up)}
                r="4"
                fill="var(--bg)"
                stroke="var(--up)"
                strokeWidth="2"
              />
            </>
          )}

          <g fill="var(--text-3)" fontFamily="var(--mono)" fontSize="10">
            <text x="4" y="18">
              {yLabel(metrics.yMax)}
            </text>
            <text x="4" y="66">
              {yLabel(metrics.yMax * (2 / 3))}
            </text>
            <text x="4" y="114">
              {yLabel(metrics.yMax * (1 / 3))}
            </text>
            <text x="4" y="162">
              0
            </text>
            <text x={W - 6} y="192" textAnchor="end">
              now
            </text>
            <text x={W / 2} y="192" textAnchor="middle">
              -{Math.round((data.length - 1) / 2)}s
            </text>
            {(data.length > 1) && (
              <text x="6" y="192">
                -{data.length - 1}s
              </text>
            )}
          </g>
        </svg>

        {activePoint && active != null && (
          <div className="tooltip" style={{ left: `${(x(active) / W) * 100}%` }}>
            <div className="tt-time mono">-{secondsAgo}s · {clockLabel(secondsAgo)}</div>
            <div className="tt-row">
              <i className="swatch" style={{ background: "var(--down)" }} />
              Download
              <b className="t-down">{formatRate(activePoint.down, "mBs", 2)}</b>
            </div>
            <div className="tt-row" style={{ marginTop: 4 }}>
              <i className="swatch" style={{ background: "var(--up)" }} />
              Upload
              <b className="t-up">{formatRate(activePoint.up, "mBs", 2)}</b>
            </div>
          </div>
        )}
      </div>

      <div className="graph-stats">
        <span>
          min <b>{formatRate(metrics.min, "mBs", 1)}</b>
        </span>
        <span>
          avg <b>{formatRate(metrics.avg, "mBs", 1)}</b>
        </span>
        <span>
          max <b>{formatRate(metrics.max, "mBs", 1)}</b>
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
