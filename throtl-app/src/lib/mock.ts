// Deterministic mock data for UI development and screenshots (VITE_MOCK=1).
// Values mirror the approved mockup so the built UI can be compared 1:1.

import type { AppRow, DashboardModel, HistoryPoint, Talker } from "./model";

const MBPS = 8000; // kbit/s per MB/s

const KNOWN_GRADIENTS: Record<string, string> = {
  Steam: "linear-gradient(140deg,#7dd3fc,#0ea5e9)",
  Firefox: "linear-gradient(140deg,#fdba74,#f97316)",
  Spotify: "linear-gradient(140deg,#bbf7d0,#22c55e)",
  immich: "linear-gradient(140deg,#e9d5ff,#a855f7)",
  vpn: "linear-gradient(140deg,#fecaca,#ef4444)",
  "(not matched)": "linear-gradient(140deg,#cbd5e1,#64748b)",
};

const PALETTE = [
  ["#7dd3fc", "#0ea5e9"],
  ["#fdba74", "#f97316"],
  ["#bbf7d0", "#22c55e"],
  ["#e9d5ff", "#a855f7"],
  ["#fecaca", "#ef4444"],
  ["#a5f3fc", "#06b6d4"],
  ["#fde68a", "#eab308"],
];

function hash(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return h;
}

export function gradientFor(name: string): string {
  if (KNOWN_GRADIENTS[name]) return KNOWN_GRADIENTS[name];
  const [a, b] = PALETTE[hash(name) % PALETTE.length];
  return `linear-gradient(140deg,${a},${b})`;
}

export function initialFor(name: string): string {
  if (name.startsWith("(")) return "?";
  const clean = name.replace(/[^A-Za-z0-9]/g, "");
  if (!clean) return "?";
  return clean.length > 1 && clean[0] === clean[0].toLowerCase()
    ? clean.slice(0, 2)
    : clean[0].toUpperCase();
}

function spark(seed: number, n = 11, lo = 6, hi = 20): number[] {
  const out: number[] = [];
  for (let i = 0; i < n; i += 1) {
    const v = Math.sin(seed * 1.7 + i * 0.9) * 0.5 + 0.5;
    out.push(lo + v * (hi - lo));
  }
  return out;
}

function history(): HistoryPoint[] {
  const n = 61;
  const rawDown: number[] = [];
  const rawUp: number[] = [];
  for (let i = 0; i < n; i += 1) {
    const p = i / (n - 1);
    rawDown.push(
      Math.max(0.1, 2.0 + Math.pow(p, 0.6) * 10 + Math.sin(i * 0.55) * 1.6 + Math.sin(i * 1.9) * 0.7),
    );
    rawUp.push(0.3 + Math.abs(Math.sin(i * 0.4)) * 0.12);
  }
  // Normalise so the mock graph matches the approved mockup: min 0.2, max
  // 13.4 MB/s and an average near 7.8 MB/s.
  const dMin = Math.min(...rawDown);
  const dMax = Math.max(...rawDown);
  const uMax = Math.max(...rawUp);
  return rawDown.map((d, i) => ({
    t: i,
    down: (0.2 + ((d - dMin) / (dMax - dMin)) * (13.4 - 0.2)) * MBPS,
    up: (rawUp[i] / uMax) * 0.42 * MBPS,
  }));
}

const APPS: AppRow[] = [
  {
    key: "steam",
    name: "Steam",
    initial: "S",
    gradient: gradientFor("Steam"),
    meta: "2 processes · pid 2211, 2247",
    downKbit: 5.65 * MBPS,
    upKbit: 0.09 * MBPS,
    downloadLimit: 3.4 * MBPS,
    uploadLimit: 0.5 * MBPS,
    priority: "hoch",
    windowLabel: "Mo–Fr 20:00–00:00",
    windowActive: true,
    windowState: null,
    budget: { used: 14e9, limit: 20e9, ratio: 0.7 },
    spark: spark(1, 11, 4, 18),
    armed: true,
    unattributed: false,
  },
  {
    key: "firefox",
    name: "Firefox",
    initial: "F",
    gradient: gradientFor("Firefox"),
    meta: "2 processes · pid 41882, 41901",
    downKbit: 1.87 * MBPS,
    upKbit: 0.11 * MBPS,
    downloadLimit: null,
    uploadLimit: null,
    priority: "normal",
    windowLabel: null,
    windowActive: false,
    windowState: null,
    budget: null,
    spark: spark(2, 11, 8, 17),
    armed: false,
    unattributed: false,
  },
  {
    key: "spotify",
    name: "Spotify",
    initial: "Sp",
    gradient: gradientFor("Spotify"),
    meta: "1 process · pid 9077",
    downKbit: 0.71 * MBPS,
    upKbit: 0.02 * MBPS,
    downloadLimit: 0.04 * MBPS,
    uploadLimit: 0.008 * MBPS,
    priority: "niedrig",
    windowLabel: "Mo–Fr 20:00–00:00",
    windowActive: false,
    windowState: "off-hours · starts 22:00",
    budget: { used: 17.6e9, limit: 20e9, ratio: 0.88 },
    spark: spark(3, 11, 11, 15),
    armed: true,
    unattributed: false,
  },
  {
    key: "not-matched",
    name: "(not matched)",
    initial: "?",
    gradient: gradientFor("(not matched)"),
    meta: "3 processes · no rule applies",
    downKbit: 0.34 * MBPS,
    upKbit: 0.01 * MBPS,
    downloadLimit: null,
    uploadLimit: null,
    priority: "normal",
    windowLabel: null,
    windowActive: false,
    windowState: null,
    budget: null,
    spark: [],
    armed: false,
    unattributed: true,
  },
];

const TALKERS: Talker[] = [
  { name: "Steam", initial: "S", gradient: gradientFor("Steam"), value: 5.65, ratio: 0.92 },
  { name: "Firefox", initial: "F", gradient: gradientFor("Firefox"), value: 2.71, ratio: 0.44 },
  { name: "Spotify", initial: "Sp", gradient: gradientFor("Spotify"), value: 1.34, ratio: 0.22 },
  { name: "immich", initial: "I", gradient: gradientFor("immich"), value: 0.88, ratio: 0.14 },
  { name: "vpn", initial: "V", gradient: gradientFor("vpn"), value: 0.47, ratio: 0.08 },
];

export function mockModel(): DashboardModel {
  return {
    enabled: true,
    profile: "Evening",
    unit: "mBs",
    downKbit: 10.4 * MBPS,
    upKbit: 0.42 * MBPS,
    matchedApps: 6,
    trendDownPct: 12,
    trendUpPct: 4,
    activeRules: 6,
    totalRules: 9,
    scheduledRules: 3,
    globalDownLimit: 20 * MBPS,
    globalUpLimit: 5 * MBPS,
    globalPriority: "normal",
    history: history(),
    windowSumBytes: 1.24e9,
    apps: APPS.map((a) => ({ ...a })),
    topTalkers: TALKERS.map((t) => ({ ...t })),
    groupsVisible: 4,
    groupsTotal: 9,
    sortKey: "download",
  };
}

/** Advance the model one tick so the UI/graph move during development. */
export function tickModel(model: DashboardModel, tick: number): DashboardModel {
  const wobble = Math.sin(tick * 0.6) * 0.04 + Math.sin(tick * 1.7) * 0.02;
  const downKbit = (10.4 + wobble * 10) * MBPS;
  const upKbit = (0.42 + Math.sin(tick * 0.9) * 0.03) * MBPS;
  const next = model.history.slice(1);
  next.push({ t: model.history[model.history.length - 1].t + 1, down: downKbit, up: upKbit });
  return { ...model, downKbit, upKbit, history: next };
}
