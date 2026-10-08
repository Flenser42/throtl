import { useEffect, useState } from "react";

import { getBudgets, removeBudget, setBudget } from "../lib/api";
import { formatBytes, parseSize } from "../lib/format";
import type { Budgets } from "../lib/types";
import { Sheet } from "./Sheet";
import { X } from "./icons";

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
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = async () => {
    try {
      const b = await getBudgets();
      setBudgets(b);
      const g = b.entries.find((e) => e.scope === "global" && e.window === "day");
      const w = b.entries.find((e) => e.scope === "global" && e.window === "week");
      setDay(g ? formatBytes(g.limit, 0) : "");
      setWeek(w ? formatBytes(w.limit, 0) : "");
      setFailed(false);
    } catch {
      setFailed(true);
      onToast("Could not load the budgets", "error");
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await setBudget({ day: parseSize(day), week: parseSize(week) });
      onToast("Global budgets saved");
      await load();
    } catch (error) {
      onToast(`Could not save the budgets: ${String(error)}`, "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (app: string) => {
    try {
      await removeBudget(app);
      onToast("Budget removed");
      await load();
    } catch (error) {
      onToast(`Could not remove the budget: ${String(error)}`, "error");
    }
  };

  const global = (budgets?.entries ?? []).filter((e) => e.scope === "global");
  const perApp = (budgets?.entries ?? []).filter((e) => e.scope === "app");

  return (
    <Sheet
      title="Budgets"
      subtitle="Rolling volume limits (last 24 h / 7 days)"
      onClose={onClose}
      footer={
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy}
          onClick={() => void save()}
        >
          Save limits
        </button>
      }
    >
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
        {failed && budgets === null && (
          <p className="sheet-note">The daemon did not answer — reopen the sheet to retry.</p>
        )}
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
              onClick={() => void remove(e.app ?? "")}
            >
              <X size={14} />
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
              ? "var(--warn)"
              : undefined,
          }}
        />
      </span>
    </div>
  );
}
