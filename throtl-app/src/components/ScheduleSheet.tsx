import { useEffect, useState } from "react";

import { getProfiles, getSchedule, setSchedule } from "../lib/api";
import { isMock } from "../lib/ipc";
import type { ScheduleEntry } from "../lib/types";
import { Sheet } from "./Sheet";
import { Plus, Trash } from "./icons";

const DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

/** A schedule entry plus a stable key, so removing a row cannot shift state. */
type Row = ScheduleEntry & { id: string };
let nextId = 0;
const makeId = () => `sched-${(nextId += 1)}`;

interface Props {
  onClose: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
  onChanged: () => void;
}

/** Time-of-day → profile rules (`set_schedule`). */
export function ScheduleSheet({ onClose, onToast, onChanged }: Props) {
  const [profiles, setProfiles] = useState<string[]>([]);
  const [entries, setEntries] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const data = await getProfiles();
        setProfiles(data.profiles);
        const rows = await getSchedule();
        setEntries(rows.map((row) => ({ ...row, id: makeId() })));
      } catch {
        /* leave the sheet empty */
      }
    })();
  }, []);

  const update = (index: number, patch: Partial<ScheduleEntry>) =>
    setEntries((prev) => prev.map((e, i) => (i === index ? { ...e, ...patch } : e)));

  const remove = (index: number) =>
    setEntries((prev) => prev.filter((_, i) => i !== index));

  const add = () =>
    setEntries((prev) => [
      ...prev,
      {
        id: makeId(),
        profile: profiles[0] ?? "Standard",
        days: [0, 1, 2, 3, 4],
        start: "18:00",
        end: "22:00",
      },
    ]);

  const toggleDay = (index: number, day: number) => {
    const current = entries[index].days;
    update(index, {
      days: current.includes(day)
        ? current.filter((d) => d !== day)
        : [...current, day].sort((a, b) => a - b),
    });
  };

  const save = async () => {
    if (!isMock) {
      setBusy(true);
      try {
        await setSchedule(entries.map(({ id: _id, ...rest }) => rest));
      } catch (error) {
        onToast(`Failed: ${String(error)}`, "error");
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    onToast("Schedule saved");
    onChanged();
    onClose();
  };

  return (
    <Sheet
      title="Schedule"
      subtitle="Switch profile by day and time"
      onClose={onClose}
      width={520}
    >
      {entries.length === 0 && (
        <p className="sheet-note">
          No schedule yet. Add a rule to switch to a profile during a time window.
        </p>
      )}

      {entries.map((entry, index) => (
        <section className="sheet-group schedule-entry" key={entry.id}>
          <div className="schedule-head">
            <div className="sheet-input-wrap">
              <select
                className="sheet-input"
                value={entry.profile}
                onChange={(e) => update(index, { profile: e.target.value })}
              >
                {profiles.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="btn danger"
              aria-label="Remove schedule rule"
              onClick={() => remove(index)}
            >
              <Trash size={14} />
            </button>
          </div>
          <div className="days">
            {DAYS.map((d, i) => (
              <button
                key={d}
                type="button"
                className={`day${entry.days.includes(i) ? " on" : ""}`}
                aria-pressed={entry.days.includes(i)}
                onClick={() => toggleDay(index, i)}
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
                value={entry.start}
                onChange={(e) => update(index, { start: e.target.value })}
              />
              <span className="time-dash">–</span>
              <input
                type="time"
                className="sheet-input"
                value={entry.end}
                onChange={(e) => update(index, { end: e.target.value })}
              />
            </div>
          </div>
        </section>
      ))}

      <div className="sheet-actions">
        <button type="button" className="btn" onClick={add}>
          <Plus size={14} /> Add rule
        </button>
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>
          Save
        </button>
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
      </div>
      <p className="sheet-note">
        A rule with no day selected is ignored. Overnight windows (start &gt; end) run
        into the next day.
      </p>
    </Sheet>
  );
}
