import { useMemo, useState } from "react";

import { parseRateInUnit, splitRate } from "../lib/format";
import { setAutostart as persistAutostart } from "../lib/ipc";
import type { DashboardModel, GlobalValues, RuleValues } from "../lib/model";
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

interface Props {
  model: DashboardModel;
  onClose: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
  onSaveProfile: (name: string) => Promise<void>;
  onSaveGlobal: (values: GlobalValues) => Promise<void>;
  onCreateRule: (values: RuleValues) => Promise<void>;
}

const STEPS = ["Profile", "Global caps", "First rule", "Background"];

/**
 * Short first-run setup: name the profile, cap the whole link, add one rule
 * for the busiest app. On finish the existing save actions run in order and
 * a success toast confirms the setup.
 */
export function Wizard({
  model,
  onClose,
  onToast,
  onSaveProfile,
  onSaveGlobal,
  onCreateRule,
}: Props) {
  const unit = model.unit;
  const label = splitRate(0, unit, 0).unit;

  const suggestion = useMemo(() => {
    const candidates = model.apps.filter((a) => !a.unattributed);
    const unruled = candidates.filter((a) => !a.ruleKey);
    const pool = unruled.length > 0 ? unruled : candidates;
    return [...pool].sort((a, b) => b.downKbit - a.downKbit)[0];
  }, [model]);

  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  const [profileName, setProfileName] = useState(model.profile);
  const [gDown, setGDown] = useState("");
  const [gUp, setGUp] = useState("");
  const [ruleName, setRuleName] = useState(suggestion?.name ?? "");
  const [matchType, setMatchType] = useState<RuleValues["matchType"]>(
    (suggestion?.matchType as RuleValues["matchType"]) ?? "exe",
  );
  const [matchValue, setMatchValue] = useState(
    suggestion?.matchValue ?? suggestion?.name ?? "",
  );
  const [rDown, setRDown] = useState("");
  const [rUp, setRUp] = useState("");
  const [priority, setPriority] = useState("normal");
  const [autostart, setAutostart] = useState(true);

  const isLast = step === STEPS.length - 1;
  const ruleValid = ruleName.trim().length > 0 && matchValue.trim().length > 0;

  const finish = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const name = profileName.trim();
      if (name) await onSaveProfile(name);
      await onSaveGlobal({
        enabled: true,
        download: parseRateInUnit(gDown, unit),
        upload: parseRateInUnit(gUp, unit),
        downloadMinimum: model.globalDownMinimum,
        uploadMinimum: model.globalUpMinimum,
        downloadPriority: model.globalPriority,
        uploadPriority: model.globalUpPriority,
      });
      if (ruleValid) {
        await onCreateRule({
          name: ruleName.trim(),
          matchType,
          matchValue: matchValue.trim(),
          download: parseRateInUnit(rDown, unit),
          upload: parseRateInUnit(rUp, unit),
          priority,
          window: null,
        });
      }
      try {
        await persistAutostart(autostart);
      } catch {
        // Non-fatal: the setup still succeeds without the login preference.
      }
      onToast("Throtl is set up");
      onClose();
    } catch (err) {
      onToast(`Setup failed: ${String(err)}`, "error");
      setBusy(false);
    }
  };

  return (
    <Sheet
      title="Set up Throtl"
      subtitle="Four quick steps to get limiting"
      onClose={onClose}
      width={440}
    >
      <div className="wizard-step mono">
        {step + 1} / {STEPS.length} — {STEPS[step]}
      </div>

      {step === 0 && (
        <section className="sheet-group">
          <h3 className="sheet-group-title">Profile</h3>
          <div className="sheet-row">
            <span className="sheet-key">Name</span>
            <div className="sheet-input-wrap">
              <input
                className="sheet-input"
                value={profileName}
                placeholder="e.g. Home"
                autoFocus
                onChange={(e) => setProfileName(e.target.value)}
              />
            </div>
          </div>
          <p className="sheet-note">
            Your rules are saved together as a named profile.
          </p>
        </section>
      )}

      {step === 1 && (
        <section className="sheet-group">
          <h3 className="sheet-group-title">Caps</h3>
          <div className="sheet-row">
            <span className="sheet-key">Download</span>
            <div className="sheet-input-wrap">
              <input
                className="sheet-input"
                value={gDown}
                placeholder="unlimited"
                inputMode="decimal"
                onChange={(e) => setGDown(e.target.value)}
              />
              <span className="sheet-input-unit">{label}</span>
            </div>
          </div>
          <div className="sheet-row">
            <span className="sheet-key">Upload</span>
            <div className="sheet-input-wrap">
              <input
                className="sheet-input"
                value={gUp}
                placeholder="unlimited"
                inputMode="decimal"
                onChange={(e) => setGUp(e.target.value)}
              />
              <span className="sheet-input-unit">{label}</span>
            </div>
          </div>
          <p className="sheet-note">Empty means unlimited.</p>
        </section>
      )}

      {step === 2 && (
        <>
          <section className="sheet-group">
            <h3 className="sheet-group-title">Application</h3>
            <div className="sheet-row">
              <span className="sheet-key">Name</span>
              <div className="sheet-input-wrap">
                <input
                  className="sheet-input"
                  value={ruleName}
                  placeholder="e.g. Steam"
                  onChange={(e) => setRuleName(e.target.value)}
                />
              </div>
            </div>
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
            <p className="sheet-note">
              {suggestion
                ? `Prefilled from ${suggestion.name}, the busiest app right now.`
                : "Limit an app that uses the connection heavily."}
            </p>
          </section>
          <section className="sheet-group">
            <h3 className="sheet-group-title">Limits</h3>
            <div className="sheet-row">
              <span className="sheet-key">Download</span>
              <div className="sheet-input-wrap">
                <input
                  className="sheet-input"
                  value={rDown}
                  placeholder="unlimited"
                  inputMode="decimal"
                  onChange={(e) => setRDown(e.target.value)}
                />
                <span className="sheet-input-unit">{label}</span>
              </div>
            </div>
            <div className="sheet-row">
              <span className="sheet-key">Upload</span>
              <div className="sheet-input-wrap">
                <input
                  className="sheet-input"
                  value={rUp}
                  placeholder="unlimited"
                  inputMode="decimal"
                  onChange={(e) => setRUp(e.target.value)}
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
        </>
      )}

      {step === 3 && (
        <section className="sheet-group">
          <h3 className="sheet-group-title">Background</h3>
          <label className="sheet-check">
            <input
              type="checkbox"
              checked={autostart}
              onChange={(e) => setAutostart(e.target.checked)}
            />
            Start Throtl in the background on login
          </label>
          <p className="sheet-note">
            Starts Throtl in the background (tray) when you log in.
          </p>
        </section>
      )}

      <div className="sheet-actions">
        <button type="button" className="btn" onClick={onClose}>
          Skip
        </button>
        <span className="tour-nav" style={{ marginLeft: "auto" }}>
          <button
            type="button"
            className="btn"
            disabled={step === 0}
            onClick={() => setStep((s) => Math.max(0, s - 1))}
          >
            Back
          </button>
          {isLast ? (
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy || !ruleValid}
              onClick={() => void finish()}
            >
              {busy ? "Saving…" : "Finish"}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              disabled={step === 0 && !profileName.trim()}
              onClick={() => setStep((s) => s + 1)}
            >
              Next
            </button>
          )}
        </span>
      </div>
    </Sheet>
  );
}
