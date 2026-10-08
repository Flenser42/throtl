import { useEffect } from "react";

import { getBudgets } from "../lib/api";
import { isMock } from "../lib/ipc";
import { notify } from "../lib/notify";
import type { Budgets } from "../lib/types";

const POLL_MS = 30_000;

type BudgetAlert = NonNullable<Budgets["alerts"]>[number];

function labelOf(alert: BudgetAlert): string {
  const who = alert.scope === "global" ? "Global" : (alert.app ?? "App");
  return `${who} ${alert.window === "week" ? "weekly" : "daily"} budget`;
}

/**
 * Watch the configured budgets and raise a desktop notification for each
 * level rise the daemon reports. The daemon dedupes transitions, so a budget
 * that stays over the limit does not spam.
 */
export function useBudgetAlerts(active: boolean): void {
  useEffect(() => {
    if (isMock || !active) return;
    let stopped = false;

    const tick = async () => {
      try {
        const data = await getBudgets();
        if (stopped) return;
        for (const alert of data.alerts ?? []) {
          if (alert.level >= 2) {
            void notify("Budget exceeded", `${labelOf(alert)} is over its limit.`);
          } else if (alert.level >= 1) {
            void notify("Budget almost used", `${labelOf(alert)} passed 80 %.`);
          }
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
