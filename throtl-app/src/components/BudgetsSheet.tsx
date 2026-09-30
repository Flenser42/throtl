import { useEffect, useState } from "react";

import { getBudgets, removeBudget, setBudget } from "../lib/api";
import { formatBytes, parseSize } from "../lib/format";
import type { Budgets } from "../lib/types";
import { Sheet } from "./Sheet";

export function BudgetsSheet({
  onClose,
  onToast,
}: {
  onClose: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
}) {
  const [budgets, setBudgets] = useState<Budgets | null>(null);
  const [day, setDay] = useState("");
  const [week, setWeek] = useState("");

  const load = () =>
    getBudgets().then((b) => {
      setBudgets(b);
      const g = b.entries.find((e) => e.scope === "global" && e.window === "day");
      const w = b.entries.find((e) => e.scope === "global" && e.window === "week");
      setDay(g ? formatBytes(g.limit, 0) : "");
      setWeek(w ? formatBytes(w.limit, 0) : "");
    });

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    await setBudget({ day: parseSize(day), week: parseSize(week) });
    onToast("Global budgets saved");
    void load();
  };

  const global = (budgets?.entries ?? []).filter((e) => e.scope === "global");
  const perApp = (budgets?.entries ?? []).filter((e) => e.scope === "app");

  return (
    <Sheet title="Budgets" subtitle="Rolling volume limits (last 24 h / 7 days)" onClose={onClose}>
      <section className="sheet-group">
        <h3 className="sheet-group-title">All applications</h3>
        <div className="sheet-row">
          <span className="sheet-key">Daily limit</span>
          <input
            className="sheet-input"
            value={day}
            placeholder="unlimited"
            onChange={(e) => setDay(e.target.value)}
          />
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Weekly limit</span>
          <input
            className="sheet-input"
            value={week}
            placeholder="unlimited"
            onChange={(e) => setWeek(e.target.value)}
          />
        </div>
        <div className="sheet-actions">
          <button type="button" className="btn btn-primary" onClick={() => void save()}>
            Save limits
          </button>
        </div>
        {global.map((e) => (
          <BudgetBar key={e.window} label={`Global · ${e.window}`} entry={e} />
        ))}
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Per application</h3>
        {perApp.length === 0 && <p className="sheet-note">No per-application budget yet.</p>}
        {perApp.map((e) => (
          <div className="budget-item" key={`${e.app}-${e.window}`}>
            <BudgetBar label={`${e.app} · ${e.window}`} entry={e} />
            <button
              type="button"
              className="overflow"
              aria-label={`Remove budget for ${e.app}`}
              onClick={() => {
                void removeBudget(e.app ?? "").then(() => {
                  onToast("Budget removed");
                  void load();
                });
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </section>
    </Sheet>
  );
}

function BudgetBar({
  label,
  entry,
}: {
  label: string;
  entry: { used: number; limit: number; ratio: number; exceeded: boolean };
}) {
  const over = entry.ratio >= 0.8;
  return (
    <div className="budget-bar">
      <div className="budget-head">
        <span className="sheet-key">{label}</span>
        <span className={`budget-value mono${over ? " warn" : ""}`}>
          {formatBytes(entry.used, 1)} / {formatBytes(entry.limit, 0)}
        </span>
      </div>
      <span className="bar">
        <i
          style={{
            width: `${Math.round(Math.min(1, entry.ratio) * 100)}%`,
            background: over
              ? "linear-gradient(90deg,var(--warn),color-mix(in oklab,var(--warn) 55%,white))"
              : undefined,
          }}
        />
      </span>
    </div>
  );
}
