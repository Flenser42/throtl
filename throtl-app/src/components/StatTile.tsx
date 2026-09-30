import type { ReactNode } from "react";

import { Sparkline } from "./Sparkline";

interface Props {
  label: string;
  value: ReactNode;
  unit?: string;
  valueClass?: string;
  trend?: string;
  caption: string;
  spark?: number[];
  sparkColor?: string;
  bars?: boolean;
}

function MiniBars() {
  const heights = [18, 26, 20, 32, 24, 36, 28, 22, 30];
  const opacity = [0.35, 0.45, 0.4, 0.6, 0.5, 0.85, 0.65, 0.5, 0.7];
  return (
    <svg className="spark" viewBox="0 0 220 42" preserveAspectRatio="none">
      <g fill="var(--accent)">
        {heights.map((h, i) => (
          <rect
            key={i}
            x={6 + i * 24}
            y={40 - h}
            width={16}
            height={h}
            rx={4}
            opacity={opacity[i]}
          />
        ))}
      </g>
    </svg>
  );
}

export function StatTile({
  label,
  value,
  unit,
  valueClass = "",
  trend,
  caption,
  spark,
  sparkColor = "var(--down)",
  bars = false,
}: Props) {
  return (
    <div className="card tile">
      <div className="tile-label">{label}</div>
      <div className={`tile-value mono ${valueClass}`}>
        {value}
        {unit && <span className="unit">{unit}</span>}
      </div>
      <div className="tile-cap">
        {trend && <span className="chip-trend">{trend}</span>}
        {caption}
      </div>
      {bars ? (
        <MiniBars />
      ) : (
        spark && <Sparkline className="spark" data={spark} color={sparkColor} />
      )}
    </div>
  );
}
