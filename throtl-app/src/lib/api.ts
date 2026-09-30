// Data access for the secondary sheets (Statistics, Budgets, Profiles).
// Routes to the Rust bridge inside Tauri, or to the deterministic mock in the
// browser so the UI can be developed and screenshotted without a daemon.

import { invokeDaemon, isMock } from "./ipc";
import { mockBudgets, mockProfiles, mockStats, mockStatsHistory } from "./mock";
import type { Budgets, Profiles, StatsApp, StatsHistory } from "./types";

export interface StatsPayload {
  window: string;
  apps: StatsApp[];
  totals: { download: number; upload: number };
}

export async function getStats(window: string): Promise<StatsPayload> {
  return isMock
    ? mockStats(window)
    : invokeDaemon<StatsPayload>("get_stats", { window });
}

export async function getStatsHistory(window: string): Promise<StatsHistory> {
  return isMock
    ? mockStatsHistory(window)
    : invokeDaemon<StatsHistory>("get_stats_history", { window });
}

export async function getBudgets(): Promise<Budgets> {
  return isMock ? mockBudgets() : invokeDaemon<Budgets>("get_budgets");
}

export async function getProfiles(): Promise<Profiles> {
  return isMock
    ? mockProfiles()
    : invokeDaemon<Profiles>("list_profiles");
}

export async function setBudget(params: Record<string, unknown>): Promise<void> {
  if (!isMock) await invokeDaemon("set_budget", params);
}

export async function removeBudget(app: string): Promise<void> {
  if (!isMock) await invokeDaemon("remove_budget", { app });
}

export async function activateProfile(name: string): Promise<void> {
  if (!isMock) await invokeDaemon("activate_profile", { name });
}
