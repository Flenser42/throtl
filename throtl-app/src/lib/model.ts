import type { ConnectionState, Unit, Window } from "./types";

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
  matchType?: string;
  matchValue?: string;
  /** Rule key, needed to remove/update the rule via the daemon. */
  ruleKey?: string;
  /** Raw time window (kept for the editor; labels live in windowLabel). */
  window?: Window | null;
}

/** The fields a rule editor can change/create. */
export interface RuleValues {
  name: string;
  matchType: "exe" | "name" | "cmdline";
  matchValue: string;
  download: number | null;
  upload: number | null;
  priority: string;
  window: Window | null;
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
