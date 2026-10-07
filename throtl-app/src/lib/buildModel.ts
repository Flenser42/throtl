import { formatWindow } from "./format";
import { initialFor } from "./mock";
import type { AppRow, DashboardModel, HistoryPoint, Talker } from "./model";
import type { AppEntry, BudgetEntry, Budgets, Config, ProcessState, Rule } from "./types";

function toMinutes(text: string): number {
  const [h, m] = text.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function ruleActive(
  win: { days: number[]; start: string; end: string },
  now = new Date(),
): boolean {
  const day = (now.getDay() + 6) % 7; // Monday = 0
  const minutes = now.getHours() * 60 + now.getMinutes();
  const start = toMinutes(win.start);
  const end = toMinutes(win.end);
  const overnight = start > end;

  if (win.days.includes(day)) {
    // Starts today; on an overnight window it runs until tomorrow's end time.
    return overnight ? minutes >= start : minutes >= start && minutes < end;
  }
  if (overnight) {
    // Reached via the previous day's start: only the tail (< end) applies.
    const prev = (day + 6) % 7;
    if (win.days.includes(prev)) return minutes < end;
  }
  return false;
}

function findRule(rules: Rule[], app: AppEntry): Rule | undefined {
  const exe = (app.exe || app.name.split(" ")[0] || "").trim();
  const base = exe.split("/").pop() || exe;
  return rules.find((r) => {
    if (r.name && r.name === app.name) return true;
    const value = (r.match_value || "").replace(/\\(.)/g, "$1");
    if (!value) return false;
    if (r.match_type === "exe") {
      // Compare full path or basename, never a bare substring (sh != bash).
      const valueBase = value.replace(/\/$/, "").split("/").pop() || value;
      return value === exe || valueBase === base;
    }
    if (r.match_type === "name") return value === base;
    return false;
  });
}

function metaFor(app: AppEntry): string {
  if (app.unattributed) return `${app.pid_count} processes · no rule applies`;
  const count = app.pid_count || app.pids.length || 1;
  const pids = (app.pids || []).slice(0, 2).join(", ");
  return `${count} ${count === 1 ? "process" : "processes"}${pids ? ` · pid ${pids}` : ""}`;
}

export function buildModel(input: {
  state: ProcessState;
  config: Config;
  budgets: Budgets;
  history: HistoryPoint[];
}): DashboardModel {
  const { state, config, budgets, history } = input;
  const rules = state.rules ?? state.processes ?? [];
  const appBudget = new Map<string, BudgetEntry>();
  for (const entry of budgets.entries ?? []) {
    if (entry.scope === "app" && entry.app) appBudget.set(entry.app.toLowerCase(), entry);
  }

  const apps: AppRow[] = (state.apps ?? []).map((app) => {
    // The daemon tells us which rule it applied; fall back to local matching
    // only when it did not name one. Prefer the stable rule key over the
    // display name: two rules may share a name but never a key.
    const named = app.rule_key
      ? rules.find((r) => r.key === app.rule_key)
      : app.rule_name
        ? rules.find((r) => r.name && r.name === app.rule_name)
        : undefined;
    const rule = app.unattributed ? undefined : (named ?? findRule(rules, app));
    const budget = appBudget.get(app.name.toLowerCase()) ?? null;
    const windowActive = rule?.window ? ruleActive(rule.window) : false;
    return {
      key: app.name,
      name: app.name,
      initial: initialFor(app.name),
      meta: metaFor(app),
      downKbit: app.download,
      upKbit: app.upload,
      downloadLimit: rule?.download_limit ?? null,
      uploadLimit: rule?.upload_limit ?? null,
      priority: rule?.priority ?? "normal",
      windowLabel: rule?.window ? formatWindow(rule.window) : null,
      windowActive,
      windowState: rule?.window && !windowActive ? "off-hours" : null,
      budget: budget ? { used: budget.used, limit: budget.limit, ratio: budget.ratio } : null,
      armed: rule != null && (rule.download_limit != null || rule.upload_limit != null),
      unattributed: app.unattributed,
      matchType: rule?.match_type ?? app.match_hint?.type ?? "exe",
      matchValue: rule?.match_value ?? app.match_hint?.value ?? (app.exe || app.name),
      ruleKey: rule?.key,
      window: rule?.window ?? null,
    };
  });

  const topApps = [...apps]
    .filter((a) => !a.unattributed)
    .sort((a, b) => b.downKbit + b.upKbit - (a.downKbit + a.upKbit))
    .slice(0, 5);
  const topTalkers: Talker[] = topApps.map((t) => ({ name: t.name }));

  const active = rules.filter(
    (r) => r.download_limit != null || r.upload_limit != null,
  ).length;

  return {
    enabled: config.global.enabled,
    profile: config.active_profile || "Standard",
    unit: config.unit,
    downKbit: state.global.download ?? 0,
    upKbit: state.global.upload ?? 0,
    matchedApps: apps.filter((a) => !a.unattributed).length,
    activeRules: active,
    totalRules: rules.length,
    globalDownLimit: config.global.download_limit,
    globalUpLimit: config.global.upload_limit,
    globalDownMinimum: config.global.download_minimum ?? 0,
    globalUpMinimum: config.global.upload_minimum ?? 0,
    globalPriority: config.global.download_priority,
    globalUpPriority: config.global.upload_priority,
    history,
    apps,
    topTalkers,
    groupsVisible: apps.length,
    groupsTotal: apps.length,
  };
}
