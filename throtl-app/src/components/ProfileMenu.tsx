import { useEffect, useState } from "react";

import {
  activateProfile,
  deleteProfile,
  getConfig,
  getProfiles,
  saveProfile,
  setStartProfile,
} from "../lib/api";
import { isMock } from "../lib/ipc";
import { Check, Plus, Trash } from "./icons";

interface Props {
  onClose: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
  /** Called after a change so the dashboard can refetch. */
  onChanged: () => void;
  /** Open the schedule editor. */
  onSchedules: () => void;
}

/** Profile switcher popover: activate, save-as, delete and pick the startup profile. */
export function ProfileMenu({ onClose, onToast, onChanged, onSchedules }: Props) {
  const [profiles, setProfiles] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [start, setStart] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const data = await getProfiles();
      setProfiles(data.profiles);
      setActive(data.active);
      if (!isMock) {
        const config = await getConfig();
        setStart(config.start_profile ?? null);
      }
    } catch {
      /* keep quiet; the popover stays empty */
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const run = async (fn: () => Promise<void>, message: string) => {
    if (!isMock) {
      setBusy(true);
      try {
        await fn();
      } catch (error) {
        onToast(`Failed: ${String(error)}`, "error");
        setBusy(false);
        // Drop the optimistic change and show what the daemon really has.
        await load();
        return;
      }
      setBusy(false);
    }
    onToast(message);
    onChanged();
  };

  const activate = (n: string) => {
    setActive(n);
    void run(() => activateProfile(n), `Profile “${n}” activated`);
    onClose();
  };

  const save = () => {
    const n = name.trim();
    if (!n) return;
    setProfiles((prev) => (prev.includes(n) ? prev : [...prev, n]));
    setActive(n);
    setName("");
    void run(() => saveProfile(n, true), `Saved as “${n}”`);
  };

  const remove = (n: string) => {
    setProfiles((prev) => prev.filter((p) => p !== n));
    if (start === n) setStart(null);
    void run(() => deleteProfile(n), `Deleted “${n}”`);
  };

  const toggleStart = (n: string) => {
    const next = start === n ? null : n;
    setStart(next);
    void run(
      () => setStartProfile(next),
      next ? `Startup profile: “${next}”` : "Startup profile cleared",
    );
  };

  return (
    <div className="popover profile-pop" role="dialog" aria-label="Profiles">
      <div className="pop-title">Profiles</div>
      <ul className="pop-list">
        {profiles.map((p) => (
          <li key={p} className={p === active ? "on" : ""}>
            <button type="button" className="pop-item" onClick={() => activate(p)}>
              <span className="pop-check">{p === active && <Check size={14} />}</span>
              <span className="pop-name">{p}</span>
              {start === p && <span className="pop-tag">startup</span>}
            </button>
            <button
              type="button"
              className="pop-star"
              title={start === p ? "Clear startup profile" : "Use at startup"}
              aria-label={`Set ${p} as startup profile`}
              disabled={busy}
              onClick={() => toggleStart(p)}
            >
              {start === p ? "★" : "☆"}
            </button>
            <button
              type="button"
              className="pop-del"
              title={`Delete ${p}`}
              aria-label={`Delete profile ${p}`}
              disabled={busy || profiles.length <= 1}
              onClick={() => remove(p)}
            >
              <Trash size={13} />
            </button>
          </li>
        ))}
      </ul>
      <div className="pop-new">
        <input
          className="sheet-input"
          value={name}
          placeholder="Save current as…"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
        />
        <button type="button" className="btn btn-primary" disabled={!name.trim() || busy} onClick={save}>
          <Plus size={13} /> Save
        </button>
      </div>
      <button
        type="button"
        className="pop-schedules"
        onClick={() => {
          onClose();
          onSchedules();
        }}
      >
        Schedule…
      </button>
    </div>
  );
}
