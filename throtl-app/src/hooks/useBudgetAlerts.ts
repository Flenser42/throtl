import { useEffect, useRef } from "react";

import { getBudgets } from "../lib/api";
import { isMock } from "../lib/ipc";
import { notify } from "../lib/notify";
import type { BudgetEntry } from "../lib/types";

const POLL_MS = 30_000;
const WARN = 0.8;

function keyOf(entry: BudgetEntry): string {
  return `${entry.scope}:${entry.app ?? ""}:${entry.window}`;
}

/** 0 = fine, 1 = past 80 %, 2 = over the limit. */
function levelOf(entry: BudgetEntry): number {
  if (entry.ratio >= 1 || entry.exceeded) return 2;
  if (entry.ratio >= WARN) return 1;
  return 0;
}

function labelOf(entry: BudgetEntry): string {
  const who = entry.scope === "global" ? "Global" : (entry.app ?? "App");
  return `${who} ${entry.window === "week" ? "weekly" : "daily"} budget`;
}

/**
 * Watch the configured budgets and raise a desktop notification when one
 * crosses 80 % or its limit. Only transitions notify, so a budget that stays
 * over the limit does not spam.
 */
export function useBudgetAlerts(active: boolean): void {
  const stateRef = useRef<Map<string, number>>(new Map());

  useEffect(() => {
    if (isMock || !active) return;
    let stopped = false;

    const tick = async () => {
      try {
        const data = await getBudgets();
        if (stopped) return;
        for (const entry of data.entries ?? []) {
          const key = keyOf(entry);
          const now = levelOf(entry);
          const before = stateRef.current.get(key) ?? 0;
          if (now > before) {
            if (now === 2) {
              void notify("Budget exceeded", `${labelOf(entry)} is over its limit.`);
            } else {
              void notify("Budget almost used", `${labelOf(entry)} passed 80 %.`);
            }
          }
          stateRef.current.set(key, now);
        }
      } catch {
        /* daemon offline — try again on the next tick */
      }
    };

    void tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [active]);
}
