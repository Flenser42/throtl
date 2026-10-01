// Types mirror the daemon's newline-delimited JSON-RPC payloads
// (see throtl/protocol.py and throtl/daemon.py). Rates are in kbit/s.

export type Unit = "kbps" | "mbps" | "kBs" | "mBs";
export type Priority = "kritisch" | "hoch" | "normal" | "niedrig";
export type ConnectionState = "connecting" | "connected" | "offline" | "denied";

export interface Rule {
  key: string;
  name: string;
  match_type: "exe" | "name" | "cmdline";
  match_value: string;
  download_limit: number | null;
  upload_limit: number | null;
  priority: Priority;
  recursive: boolean;
  window?: Window | null;
}

export interface Window {
  days: number[];
  start: string;
  end: string;
}

export interface AppEntry {
  name: string;
  exe: string;
  download: number;
  upload: number;
  pids: string[];
  pid_count: number;
  unattributed: boolean;
  rule_name?: string | null;
}

export interface ProcessState {
  interface: string;
  enabled: boolean;
  apps: AppEntry[];
  processes: Rule[];
  rules: Rule[];
  global: { download: number | null; upload: number | null };
  attributed?: { download: number; upload: number };
}

export interface DaemonStatus {
  simulated?: boolean;
  enabled?: boolean;
  monitoring?: boolean;
  daemon?: string;
  active_profile?: string;
  [k: string]: unknown;
}

export interface Config {
  unit: Unit;
  active_profile: string;
  start_profile: string | null;
  schedule?: ScheduleEntry[];
  global: {
    enabled: boolean;
    download_limit: number | null;
    upload_limit: number | null;
    download_minimum: number;
    upload_minimum: number;
    download_priority: Priority;
    upload_priority: Priority;
  };
  processes: Rule[];
}

export interface BudgetEntry {
  scope: "global" | "app";
  app: string | null;
  window: "day" | "week";
  used: number;
  limit: number;
  ratio: number;
  exceeded: boolean;
}

export interface Budgets {
  enabled: boolean;
  entries: BudgetEntry[];
}

export interface StatsApp {
  app: string;
  download: number;
  upload: number;
}

export interface StatsHistory {
  window: string;
  series: { id: number | null; download: number; upload: number }[];
}

export interface Profiles {
  profiles: string[];
  active: string;
}

/** A time-of-day → profile rule (`set_schedule`). */
export interface ScheduleEntry {
  profile: string;
  days: number[];
  start: string;
  end: string;
}
