import { useState } from "react";

import { parseRateInUnit, splitRate } from "../lib/format";
import type { AppRow, RuleValues } from "../lib/model";
import type { Unit, Window } from "../lib/types";
import { Sheet } from "./Sheet";

const PRIORITIES = [
  { id: "kritisch", label: "Critical" },
  { id: "hoch", label: "High" },
  { id: "normal", label: "Normal" },
  { id: "niedrig", label: "Low" },
];

const MATCH_TYPES = [
  { id: "exe", label: "Executable" },
  { id: "name", label: "Process name" },
  { id: "cmdline", label: "Command line" },
] as const;

const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

interface Props {
  mode: "create" | "edit";
  /** Existing row (edit) or the row to prefill from (create). */
  app?: AppRow;
  unit: Unit;
  onSave: (values: RuleValues) => void;
  onDelete?: () => void;
  onClose: () => void;
}

export function RuleSheet({ mode, app, unit, onSave, onDelete, onClose }: Props) {
  const isCreate = mode === "create";
  const [name, setName] = useState(app?.name ?? "");
  const [matchType, setMatchType] = useState<RuleValues["matchType"]>(
    (app?.matchType as RuleValues["matchType"]) ?? "exe",
  );
  const [matchValue, setMatchValue] = useState(app?.matchValue ?? app?.name ?? "");
  const [down, setDown] = useState(
    app?.downloadLimit != null ? splitRate(app.downloadLimit, unit, 2).value : "",
  );
  const [up, setUp] = useState(
    app?.uploadLimit != null ? splitRate(app.uploadLimit, unit, 2).value : "",
  );
  const [priority, setPriority] = useState(app?.priority ?? "normal");
  const [windowOn, setWindowOn] = useState(Boolean(app?.window));
  const [days, setDays] = useState<number[]>(app?.window?.days ?? [0, 1, 2, 3, 4]);
  const [start, setStart] = useState(app?.window?.start ?? "22:00");
  const [end, setEnd] = useState(app?.window?.end ?? "06:00");

  const label = splitRate(0, unit, 0).unit;
  const valid = name.trim().length > 0 && matchValue.trim().length > 0;

  const toggleDay = (day: number) =>
    setDays((prev) => (prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day]));

  const submit = () => {
    const win: Window | null =
      windowOn && days.length > 0
        ? { days: [...days].sort((a, b) => a - b), start, end }
        : null;
    onSave({
      name: name.trim(),
      matchType,
      matchValue: matchValue.trim(),
      download: parseRateInUnit(down, unit),
      upload: parseRateInUnit(up, unit),
      priority,
      window: win,
    });
  };

  return (
    <Sheet
      title={isCreate ? "New rule" : (app?.name ?? "Rule")}
      subtitle={
        isCreate ? "Limit an app that has no rule yet" : "Limit, priority and window for this app"
      }
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-primary" disabled={!valid} onClick={submit}>
            {isCreate ? "Create rule" : "Save"}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          {!isCreate && onDelete && (
            <button type="button" className="btn danger push-right" onClick={onDelete}>
              Delete
            </button>
          )}
        </>
      }
    >
      <section className="sheet-group">
        <h3 className="sheet-group-title">Application</h3>
        {isCreate && (
          <div className="sheet-row">
            <span className="sheet-key">Name</span>
            <div className="sheet-input-wrap">
              <input
                className="sheet-input"
                value={name}
                placeholder="e.g. Steam"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>
        )}
        {isCreate ? (
          <>
            <div className="sheet-row">
              <span className="sheet-key">Match by</span>
              <div className="seg">
                {MATCH_TYPES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={matchType === m.id ? "on" : ""}
                    onClick={() => setMatchType(m.id)}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="sheet-row">
              <span className="sheet-key">Pattern</span>
              <div className="sheet-input-wrap">
                <input
                  className="sheet-input"
                  value={matchValue}
                  placeholder={matchType === "exe" ? "/usr/bin/steam" : "steam"}
                  onChange={(e) => setMatchValue(e.target.value)}
                />
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="sheet-row">
              <span className="sheet-key">Match by</span>
              <span className="sheet-value">
                {MATCH_TYPES.find((m) => m.id === matchType)?.label}
              </span>
            </div>
            <div className="sheet-row">
              <span className="sheet-key">Pattern</span>
              <span className="sheet-value mono">{matchValue}</span>
            </div>
            <p className="sheet-note">
              A rule's match pattern is fixed once it exists — remove the rule and
              create a new one to change what it applies to.
            </p>
          </>
        )}
      </section>

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
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Time window</h3>
        <label className="sheet-check">
          <input
            type="checkbox"
            checked={windowOn}
            onChange={(e) => setWindowOn(e.target.checked)}
          />
          Only apply during a window
        </label>
        {windowOn && (
          <>
            <div className="days">
              {DAYS.map((d, i) => (
                <button
                  key={d}
                  type="button"
                  className={`day${days.includes(i) ? " on" : ""}`}
                  aria-pressed={days.includes(i)}
                  onClick={() => toggleDay(i)}
                >
                  {d}
                </button>
              ))}
            </div>
            <div className="sheet-row">
              <span className="sheet-key">From / to</span>
              <div className="time-range">
                <input
                  type="time"
                  className="sheet-input"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
                <span className="time-dash">–</span>
                <input
                  type="time"
                  className="sheet-input"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </div>
            </div>
            {start > end && <p className="sheet-note">Overnight window (ends the next day).</p>}
          </>
        )}
      </section>
    </Sheet>
  );
}
