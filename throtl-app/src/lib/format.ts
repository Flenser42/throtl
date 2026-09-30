import type { Unit } from "./types";

// Display units, matching throtl/units.py (internal rates are kbit/s).
const UNIT_LABEL: Record<Unit, string> = {
  kbps: "kbit/s",
  mbps: "Mbit/s",
  kBs: "KB/s",
  mBs: "MB/s",
};

// factor: kbit/s -> number in that unit
const UNIT_FACTOR: Record<Unit, number> = {
  kbps: 1,
  mbps: 1 / 1000,
  kBs: 0.125,
  mBs: 0.125 / 1000,
};

export const RATE_UNITS = ["B/s", "KB/s", "MB/s", "GB/s", "TB/s"] as const;

export function trimNum(text: string): string {
  if (!text.includes(".")) return text;
  return text.replace(/0+$/, "").replace(/\.$/, "");
}

export function inUnit(kbit: number, unit: Unit): number {
  return kbit * UNIT_FACTOR[unit];
}

export function unitLabel(unit: Unit): string {
  return UNIT_LABEL[unit];
}

/** Split a kbit/s rate into a numeric string + unit for the configured unit. */
export function splitRate(
  kbit: number | null | undefined,
  unit: Unit = "mBs",
  precision = 2,
): { value: string; unit: string } {
  if (kbit == null) return { value: "unlimited", unit: "" };
  return {
    value: trimNum(inUnit(kbit, unit).toFixed(precision)),
    unit: UNIT_LABEL[unit],
  };
}

export function formatRate(
  kbit: number | null | undefined,
  unit: Unit = "mBs",
  precision = 2,
): string {
  const { value, unit: u } = splitRate(kbit, unit, precision);
  return u ? `${value} ${u}` : value;
}

/** Auto-scale a kbit/s rate to the friendliest unit (for graph axes). */
export function autoRate(kbit: number, precision = 1): { value: string; unit: string } {
  let bytes = (kbit * 1000) / 8;
  let i = 0;
  while (bytes >= 1000 && i < RATE_UNITS.length - 1) {
    bytes /= 1000;
    i += 1;
  }
  return { value: trimNum(bytes.toFixed(precision)), unit: RATE_UNITS[i] };
}

export function formatAutoRate(kbit: number, precision = 1): string {
  const { value, unit } = autoRate(kbit, precision);
  return `${value} ${unit}`;
}

const VOLUME = ["B", "KB", "MB", "GB", "TB", "PB"];

export function formatBytes(
  bytes: number,
  precision = 1,
  trimTrailing = false,
): string {
  let value = Math.max(0, bytes);
  let i = 0;
  while (value >= 1000 && i < VOLUME.length - 1) {
    value /= 1000;
    i += 1;
  }
  const text = value.toFixed(precision);
  return `${trimTrailing ? trimNum(text) : text} ${VOLUME[i]}`;
}

const DAY_TOKENS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

/** "Mo–Fr 20:00–00:00" from a window rule (English day tokens). */
export function formatWindow(win: { days: number[]; start: string; end: string }): string {
  const days = [...win.days].sort((a, b) => a - b);
  let label = days.map((d) => DAY_TOKENS[d] ?? "?").join(",");
  if (days.length >= 2) {
    let contiguous = true;
    for (let i = 1; i < days.length; i += 1) {
      if (days[i] !== days[i - 1] + 1) contiguous = false;
    }
    if (contiguous) label = `${DAY_TOKENS[days[0]]}–${DAY_TOKENS[days[days.length - 1]]}`;
  }
  return `${label} ${win.start}–${win.end}`;
}

export const PRIORITY_LABEL: Record<string, string> = {
  kritisch: "Critical",
  hoch: "High",
  normal: "Normal",
  niedrig: "Low",
};

export function priorityRank(priority: string): number {
  return { kritisch: 0, hoch: 1, normal: 2, niedrig: 3 }[priority] ?? 2;
}
