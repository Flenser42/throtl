import { useEffect, useState } from "react";

import { getStats, getStatsHistory, type StatsPayload } from "../lib/api";
import { formatBytes } from "../lib/format";
import type { StatsHistory } from "../lib/types";
import { Sheet } from "./Sheet";

const RANGES: { id: string; label: string }[] = [
  { id: "minute", label: "1 h" },
  { id: "hour", label: "2 days" },
  { id: "day", label: "30 days" },
];

export function StatisticsSheet({ onClose }: { onClose: () => void }) {
  const [range, setRange] = useState("minute");
  const [data, setData] = useState<StatsPayload | null>(null);
  const [history, setHistory] = useState<StatsHistory | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // Drop the previous range so a slow answer cannot show stale bars.
    setData(null);
    setHistory(null);
    Promise.all([getStats(range), getStatsHistory(range)])
      .then(([s, h]) => {
        if (cancelled) return;
        setData(s);
        setHistory(h);
        setFailed(false);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [range]);

  const series = history?.series ?? [];
  const peak = Math.max(1, ...series.map((p) => p.download + p.upload));
  const appPeak = Math.max(1, ...(data?.apps ?? []).map((a) => a.download + a.upload));

  return (
    <Sheet
      title="Statistics"
      subtitle={
        data ? `Total ↓ ${formatBytes(data.totals.download)} · ↑ ${formatBytes(data.totals.upload)}` : undefined
      }
      onClose={onClose}
      width={520}
    >
      <div className="seg" style={{ marginBottom: 16 }}>
        {RANGES.map((r) => (
          <button
            key={r.id}
            type="button"
            className={range === r.id ? "on" : ""}
            onClick={() => setRange(r.id)}
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className="stats-chart" aria-hidden="true">
        {series.map((p, i) => {
          const total = p.download + p.upload;
          const h = (total / peak) * 100;
          const upH = total > 0 ? (p.upload / total) * h : 0;
          return (
            <span key={i} className="stats-bar" style={{ height: `${h}%` }}>
              <i style={{ height: `${upH}%` }} />
            </span>
          );
        })}
      </div>

      {failed && !data && (
        <p className="sheet-note">The daemon did not answer — reopen the sheet to retry.</p>
      )}

      <div className="stats-list">
        {(data?.apps ?? []).map((app) => {
          const total = app.download + app.upload;
          return (
            <div className="stats-row" key={app.app}>
              <span className="stats-name">{app.app}</span>
              <span className="bar">
                <i style={{ width: `${(total / appPeak) * 100}%` }} />
              </span>
              <span className="stats-val mono">{formatBytes(total, 1)}</span>
            </div>
          );
        })}
      </div>
      <p className="sheet-note">Rates are 1-minute rolling averages.</p>
    </Sheet>
  );
}
