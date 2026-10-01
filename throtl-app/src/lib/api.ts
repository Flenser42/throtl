// Data access for everything outside the live process list: the secondary
// sheets (Statistics, Budgets, Profiles) and the write actions (rule edits,
// budgets, profiles, unit). Routes to the Rust bridge inside Tauri, or to the
// deterministic mock in the browser so the UI works without a daemon.

import { invokeDaemon, isMock } from "./ipc";
import { mockBudgets, mockProfiles, mockStats, mockStatsHistory } from "./mock";
import type { Budgets, Config, Profiles, StatsApp, StatsHistory } from "./types";

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

export async function setBudget(params: Record<string, unknown>): Promise<void> {
  if (!isMock) await invokeDaemon("set_budget", params);
}

export async function removeBudget(app: string): Promise<void> {
  if (!isMock) await invokeDaemon("remove_budget", { app });
}

export async function resetStats(): Promise<void> {
  if (!isMock) await invokeDaemon("reset_stats");
}

// ---- rules (set_process / remove_process) ----

export async function setRule(params: Record<string, unknown>): Promise<void> {
  if (!isMock) await invokeDaemon("set_process", params);
}

export async function removeRule(key: string): Promise<void> {
  if (!isMock) await invokeDaemon("remove_process", { key });
}

// ---- profiles ----

export async function getProfiles(): Promise<Profiles> {
  return isMock ? mockProfiles() : invokeDaemon<Profiles>("list_profiles");
}

export async function activateProfile(name: string): Promise<void> {
  if (!isMock) await invokeDaemon("activate_profile", { name });
}

export async function saveProfile(name: string, activate = true): Promise<void> {
  if (!isMock) await invokeDaemon("set_profile", { name, activate });
}

export async function deleteProfile(name: string): Promise<void> {
  if (!isMock) await invokeDaemon("delete_profile", { name });
}

export async function setStartProfile(name: string | null): Promise<void> {
  if (!isMock) await invokeDaemon("set_start_profile", { name: name ?? "" });
}

// ---- config / unit ----

export async function setUnit(unit: string): Promise<void> {
  if (!isMock) await invokeDaemon("set_unit", { unit });
}

export async function getConfig(): Promise<Config> {
  return invokeDaemon<Config>("get_config");
}

export async function importConfig(config: unknown): Promise<void> {
  if (!isMock) await invokeDaemon("import_config", { config });
}
