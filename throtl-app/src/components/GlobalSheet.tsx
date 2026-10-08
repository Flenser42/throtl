import { useState } from "react";

import { parseRateInUnit, splitRate } from "../lib/format";
import type { DashboardModel, GlobalValues } from "../lib/model";
import { Sheet } from "./Sheet";

const PRIORITIES = [
  { id: "kritisch", label: "Critical" },
  { id: "hoch", label: "High" },
  { id: "normal", label: "Normal" },
  { id: "niedrig", label: "Low" },
];

interface Props {
  model: DashboardModel;
  onSave: (values: GlobalValues) => void;
  onClose: () => void;
}

/** Global caps: the ceiling for the whole link, plus the per-direction floor. */
export function GlobalSheet({ model, onSave, onClose }: Props) {
  const unit = model.unit;
  const label = splitRate(0, unit, 0).unit;
  const asText = (kbit: number | null) =>
    kbit != null ? splitRate(kbit, unit, 2).value : "";

  const [enabled, setEnabled] = useState(model.enabled);
  const [down, setDown] = useState(asText(model.globalDownLimit));
  const [up, setUp] = useState(asText(model.globalUpLimit));
  const [downMin, setDownMin] = useState(asText(model.globalDownMinimum || null));
  const [upMin, setUpMin] = useState(asText(model.globalUpMinimum || null));
  const [downPriority, setDownPriority] = useState(model.globalPriority);
  const [upPriority, setUpPriority] = useState(model.globalUpPriority);

  return (
    <Sheet
      title="Global limits"
      subtitle="The ceiling applied to the whole connection"
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() =>
              onSave({
                enabled,
                download: parseRateInUnit(down, unit),
                upload: parseRateInUnit(up, unit),
                downloadMinimum: parseRateInUnit(downMin, unit) ?? 0,
                uploadMinimum: parseRateInUnit(upMin, unit) ?? 0,
                downloadPriority: downPriority,
                uploadPriority: upPriority,
              })
            }
          >
            Save
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </>
      }
    >
      <label className="sheet-check sheet-check-block">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Limit traffic at all
      </label>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Caps</h3>
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
        <h3 className="sheet-group-title">Minimum</h3>
        <div className="sheet-row">
          <span className="sheet-key">Download floor</span>
          <div className="sheet-input-wrap">
            <input
              className="sheet-input"
              value={downMin}
              placeholder="0"
              inputMode="decimal"
              onChange={(e) => setDownMin(e.target.value)}
            />
            <span className="sheet-input-unit">{label}</span>
          </div>
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Upload floor</span>
          <div className="sheet-input-wrap">
            <input
              className="sheet-input"
              value={upMin}
              placeholder="0"
              inputMode="decimal"
              onChange={(e) => setUpMin(e.target.value)}
            />
            <span className="sheet-input-unit">{label}</span>
          </div>
        </div>
        <p className="sheet-note">Traffic below the floor is never shaped.</p>
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Priority</h3>
        <div className="sheet-row">
          <span className="sheet-key">Download</span>
          <div className="seg">
            {PRIORITIES.map((p) => (
              <button
                key={p.id}
                type="button"
                className={downPriority === p.id ? "on" : ""}
                onClick={() => setDownPriority(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Upload</span>
          <div className="seg">
            {PRIORITIES.map((p) => (
              <button
                key={p.id}
                type="button"
                className={upPriority === p.id ? "on" : ""}
                onClick={() => setUpPriority(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </section>
    </Sheet>
  );
}
