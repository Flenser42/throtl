import { useState } from "react";

import { parseRateInUnit, splitRate } from "../lib/format";
import type { AppRow } from "../lib/model";
import type { Unit } from "../lib/types";
import { Sheet } from "./Sheet";

const PRIORITIES = [
  { id: "kritisch", label: "Critical" },
  { id: "hoch", label: "High" },
  { id: "normal", label: "Normal" },
  { id: "niedrig", label: "Low" },
];

interface Props {
  app: AppRow;
  unit: Unit;
  onSave: (
    app: AppRow,
    values: { download: number | null; upload: number | null; priority: string },
  ) => void;
  onClose: () => void;
}

export function RuleSheet({ app, unit, onSave, onClose }: Props) {
  const [down, setDown] = useState(
    app.downloadLimit != null ? splitRate(app.downloadLimit, unit, 2).value : "",
  );
  const [up, setUp] = useState(
    app.uploadLimit != null ? splitRate(app.uploadLimit, unit, 2).value : "",
  );
  const [priority, setPriority] = useState(app.priority);

  const label = splitRate(0, unit, 0).unit;

  return (
    <Sheet title={app.name} subtitle="Limit, priority and window for this app" onClose={onClose}>
      <section className="sheet-group">
        <h3 className="sheet-group-title">Limits</h3>
        <div className="sheet-row">
          <span className="sheet-key">Download</span>
          <div className="sheet-input-wrap">
            <input
              className="sheet-input"
              value={down}
              placeholder="unlimited"
              inputMode="decimal"
              onChange={(e) => setDown(e.target.value)}
            />
            <span className="sheet-input-unit">{label}</span>
          </div>
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Upload</span>
          <div className="sheet-input-wrap">
            <input
              className="sheet-input"
              value={up}
              placeholder="unlimited"
              inputMode="decimal"
              onChange={(e) => setUp(e.target.value)}
            />
            <span className="sheet-input-unit">{label}</span>
          </div>
        </div>
        <p className="sheet-note">Empty means unlimited.</p>
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Priority</h3>
        <div className="sheet-row">
          <span className="sheet-key">When the link is busy</span>
          <div className="seg">
            {PRIORITIES.map((p) => (
              <button
                key={p.id}
                type="button"
                className={priority === p.id ? "on" : ""}
                onClick={() => setPriority(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        {app.windowLabel && (
          <div className="sheet-row">
            <span className="sheet-key">Time window</span>
            <span className="sheet-value">
              {app.windowLabel}
              {app.windowActive ? " · now" : ""}
            </span>
          </div>
        )}
      </section>

      <div className="sheet-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() =>
            onSave(app, {
              download: parseRateInUnit(down, unit),
              upload: parseRateInUnit(up, unit),
              priority,
            })
          }
        >
          Save
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Sheet>
  );
}
