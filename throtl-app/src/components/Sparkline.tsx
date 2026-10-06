import { useMemo } from "react";

import type { HistoryPoint } from "../lib/model";

const W = 120;
const H = 28;
const PAD = 3;

interface Props {
  history: HistoryPoint[];
  /** Number of most recent samples to draw (default 40). */
  samples?: number;
}

/**
 * A tiny download-activity polyline for the header. It has no axes and no
 * per-frame animation: it simply re-renders when new data arrives (the header
 * gets a fresh history slice every poll), which already honours
 * `prefers-reduced-motion`.
 */
export function Sparkline({ history, samples = 40 }: Props) {
  const points = useMemo(() => {
    const data = history.slice(-samples).map((p) => p.down);
    if (data.length < 2) return "";
    const max = Math.max(1, ...data);
    const step = W / (data.length - 1);
    const y = (v: number) => H - PAD - (Math.min(v, max) / max) * (H - PAD * 2);
    return data
      .map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`)
      .join(" ");
  }, [history, samples]);

  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      aria-hidden="true"
      focusable="false"
    >
      {points && (
        <polyline
          points={points}
          fill="none"
          stroke="var(--graph-down)"
          strokeWidth="1.25"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}
