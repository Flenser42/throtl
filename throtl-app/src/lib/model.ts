import type { ConnectionState, Unit } from "./types";

export interface AppRow {
  key: string;
  name: string;
  initial: string;
  gradient: string;
  meta: string;
  downKbit: number;
  upKbit: number;
  downloadLimit: number | null;
  uploadLimit: number | null;
  priority: string;
  windowLabel: string | null;
  windowActive: boolean;
  /** "off-hours · starts 22:00" style label when a window exists but is off. */
  windowState: string | null;
  budget: { used: number; limit: number; ratio: number } | null;
  spark: number[];
  armed: boolean;
  unattributed: boolean;
}

export interface Talker {
  name: string;
  initial: string;
  gradient: string;
  value: number;
  ratio: number;
}

export interface HistoryPoint {
  t: number;
  down: number;
  up: number;
}

export interface DashboardModel {
  enabled: boolean;
  profile: string;
  unit: Unit;
  downKbit: number;
  upKbit: number;
  matchedApps: number;
  trendDownPct: number;
  trendUpPct: number;
  activeRules: number;
  totalRules: number;
  scheduledRules: number;
  globalDownLimit: number | null;
  globalUpLimit: number | null;
  globalPriority: string;
  history: HistoryPoint[];
  windowSumBytes: number;
  apps: AppRow[];
  topTalkers: Talker[];
  groupsVisible: number;
  groupsTotal: number;
  sortKey: "download" | "upload" | "name";
}

export interface DaemonView {
  state: ConnectionState;
  error: string | null;
  model: DashboardModel | null;
}
