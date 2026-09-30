import { useId } from "react";

interface Props {
  data: number[];
  color: string;
  height?: number;
  width?: number;
  fill?: boolean;
  strokeWidth?: number;
  className?: string;
}

/** Tiny line+area sparkline with no axes (SVG, stretches to its box). */
export function Sparkline({
  data,
  color,
  height = 42,
  width = 220,
  fill = true,
  strokeWidth = 1.6,
  className,
}: Props) {
  const gid = useId();
  if (data.length < 2) {
    return (
      <svg
        className={className}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        style={{ display: "block" }}
      />
    );
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const x = (i: number) => (i / (data.length - 1)) * width;
  const y = (v: number) => height - 3 - ((v - min) / span) * (height - 6);
  const line = data
    .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;

  return (
    <svg
      className={className}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      style={{ display: "block" }}
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor={color} stopOpacity=".38" />
              <stop offset="1" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gid})`} />
        </>
      )}
      <path d={line} fill="none" stroke={color} strokeWidth={strokeWidth} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
