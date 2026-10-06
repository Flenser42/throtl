import { useCallback, useEffect, useRef, useState } from "react";

import {
  removeRule as removeRuleApi,
  resetStats as resetStatsApi,
  setGlobal as setGlobalApi,
  setRule as setRuleApi,
  setUnit as setUnitApi,
} from "../lib/api";
import { buildModel } from "../lib/buildModel";
import { isMock, listen, invokeDaemon } from "../lib/ipc";
import { initialFor, mockModel, tickModel } from "../lib/mock";
import type {
  AppRow,
  DaemonView,
  DashboardModel,
  GlobalValues,
  HistoryPoint,
  RuleValues,
} from "../lib/model";
import type { Budgets, Config, ProcessState, Unit } from "../lib/types";

// 30 minutes at 1 Hz covers the "15m"/"all" graph windows.
const MAX_HISTORY = 1800;

type SortKey = "download" | "upload" | "name";

interface Options {
  /** Surfaces bridge/daemon errors that no caller can await (poll, events). */
  onError?: (message: string) => void;
}

/**
 * The daemon's `set_process` updates an existing rule only when it is given the
 * rule `key`; without it the daemon would mint a new, double-escaped rule. A
 * `null` limit clears it and `window: null` clears the time window, so both
 * must be sent explicitly (JSON drops `undefined`).
 */
function ruleParams(values: RuleValues, key?: string): Record<string, unknown> {
  return {
    ...(key ? { key } : {}),
    name: values.name,
    match_type: values.matchType,
    match_value: values.matchValue,
    download_limit: values.download,
    upload_limit: values.upload,
    priority: values.priority,
    window: values.window,
  };
}

function valuesFromApp(app: AppRow, download: number | null, upload: number | null): RuleValues {
  return {
    name: app.name,
    matchType: (app.matchType as RuleValues["matchType"]) ?? "exe",
    matchValue: app.matchValue ?? app.name,
    download,
    upload,
    priority: app.priority,
    window: app.window ?? null,
  };
}

function mockRow(values: RuleValues): AppRow {
  return {
    key: values.name,
    name: values.name,
    initial: initialFor(values.name),
    meta: "new rule · not running yet",
    downKbit: 0,
    upKbit: 0,
    downloadLimit: values.download,
    uploadLimit: values.upload,
    priority: values.priority,
    windowLabel: null,
    windowActive: false,
    windowState: null,
    budget: null,
    armed: values.download != null || values.upload != null,
    unattributed: false,
    matchType: values.matchType,
    matchValue: values.matchValue,
    window: values.window,
  };
}

/**
 * Owns the daemon connection. In the browser (no Tauri, or VITE_MOCK=1) it
 * drives a deterministic mock so the UI can be developed and screenshotted.
 * Inside Tauri it subscribes to the Rust bridge events and invokes commands.
 *
 * Write actions are **not** optimistic: the daemon is the single source of
 * truth, so a write is awaited and then followed by an immediate refetch. An
 * optimistic value would be replaced by the 1 Hz poll before the daemon had
 * confirmed it, which made the UI flip back and forth — and it lied whenever
 * the write failed.
 */
export function useDaemon(options: Options = {}): DaemonView & {
  toggle: (enabled: boolean) => void;
  toggleArm: (key: string, armed: boolean) => void;
  setApp: (app: AppRow, values: RuleValues) => Promise<void>;
  createRule: (values: RuleValues) => Promise<void>;
  removeRule: (app: AppRow) => Promise<void>;
  resetStats: () => Promise<void>;
  changeUnit: (unit: Unit) => void;
  saveGlobal: (values: GlobalValues) => Promise<void>;
  refresh: () => void;
  sortKey: SortKey;
  setSort: (key: SortKey) => void;
} {
  const { onError } = options;
  // `?mockstate=offline|denied|connecting` forces a connection state (screens).
  const forced = isMock
    ? new URLSearchParams(location.search).get("mockstate")
    : null;
  const forcedState =
    forced === "offline" || forced === "denied" || forced === "connecting"
      ? forced
      : null;
  const [view, setView] = useState<DaemonView>({
    state: isMock ? (forcedState ?? "connected") : "connecting",
    error:
      forcedState === "offline"
        ? "Connection refused — is the throtl service running?"
        : null,
    model: isMock && !forcedState ? mockModel() : null,
  });
  const [sortKey, setSort] = useState<SortKey>("download");
  // Latest view, so the memoised write actions always read fresh state
  // without re-creating themselves on every 1 Hz poll (rows are memoised
  // against these identities).
  const viewRef = useRef(view);
  viewRef.current = view;
  const historyRef = useRef<HistoryPoint[]>(isMock ? mockModel().history.slice(-MAX_HISTORY) : []);
  const realRef = useRef<{ state?: ProcessState; config?: Config; budgets?: Budgets }>({});
  const refreshRef = useRef<() => void>(() => {});
  // Limits remembered while a row is disarmed, so arming restores them.
  const disarmedRef = useRef<Map<string, { download: number | null; upload: number | null }>>(new Map());

  /** Mock mode has no daemon: change the view directly. */
  const mutate = (patch: (model: DashboardModel) => DashboardModel) =>
    setView((prev) => (prev.model ? { ...prev, model: patch(prev.model) } : prev));

  const refetch = useCallback(() => refreshRef.current(), []);

  // ---- mock mode ----
  useEffect(() => {
    if (!isMock || forcedState) return;
    // `?static` freezes the mock so screenshots match the mockup exactly.
    if (new URLSearchParams(location.search).has("static")) return;
    let tick = 0;
    const id = window.setInterval(() => {
      tick += 1;
      setView((prev) => (prev.model ? { ...prev, model: tickModel(prev.model, tick) } : prev));
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  // ---- real (Tauri) mode ----
  useEffect(() => {
    if (isMock) return;
    let disposed = false;
    const unlisteners: Array<() => void> = [];

    const rebuild = () => {
      if (disposed) return;
      const { state, config, budgets } = realRef.current;
      if (!state || !config) return;
      const model = buildModel({
        state,
        config,
        budgets: budgets ?? { enabled: true, entries: [] },
        history: historyRef.current,
      });
      setView({ state: "connected", error: null, model });
    };

    const fetchAll = async () => {
      try {
        const [status, config, budgets] = await Promise.all([
          invokeDaemon<{ active_profile?: string }>("status"),
          invokeDaemon<Config>("get_config"),
          invokeDaemon<Budgets>("get_budgets"),
        ]);
        if (disposed) return;
        realRef.current.config = config;
        realRef.current.budgets = budgets;
        void status;
        realRef.current.state = await invokeDaemon<ProcessState>("list_processes");
        if (disposed) return;
        rebuild();
      } catch (error) {
        if (!disposed) setView({ state: "offline", error: String(error), model: null });
      }
    };

    (async () => {
      const offUpdate = await listen<ProcessState>("daemon:update", (state) => {
        if (disposed) return;
        realRef.current.state = state;
        const point: HistoryPoint = {
          t: Date.now() / 1000,
          down: state.global.download ?? 0,
          up: state.global.upload ?? 0,
        };
        historyRef.current = [...historyRef.current.slice(-(MAX_HISTORY - 1)), point];
        rebuild();
      });
      const offState = await listen<string>("daemon:state", (state) => {
        if (disposed) return;
        if (state === "connected") {
          // (Re)fetch config so a daemon that started later brings the UI up.
          void fetchAll();
        } else if (state === "offline" || state === "denied") {
          // Drop the model too: keeping it would freeze the last graph and
          // rates on screen while the daemon is gone, with no indication.
          setView((prev) => ({ ...prev, state: state as DaemonView["state"], model: null }));
        }
      });
      const offError = await listen<string>("daemon:error", (message) => {
        if (disposed) return;
        onError?.(`Daemon: ${message}`);
      });
      const offTrayToggle = await listen<boolean>("daemon:tray-toggle", () => {
        if (disposed) return;
        void fetchAll();
      });
      if (disposed) {
        offUpdate();
        offState();
        offError();
        offTrayToggle();
        return;
      }
      unlisteners.push(offUpdate, offState, offError, offTrayToggle);
      refreshRef.current = () => {
        void fetchAll();
      };
      void fetchAll();
    })();

    return () => {
      disposed = true;
      unlisteners.forEach((off) => off());
    };
  }, [onError]);

  const toggle = useCallback(
    (enabled: boolean) => {
      if (isMock) {
        mutate((model) => ({ ...model, enabled }));
        return;
      }
      invokeDaemon("toggle", { enabled })
        .then(refetch)
        .catch((error) => onError?.(`Shaping: ${String(error)}`));
    },
    [refetch, onError],
  );

  const setApp = useCallback(
    async (app: AppRow, values: RuleValues) => {
      if (isMock) {
        mutate((model) => ({
          ...model,
          apps: model.apps.map((a) =>
            a.key === app.key
              ? {
                  ...a,
                  downloadLimit: values.download,
                  uploadLimit: values.upload,
                  priority: values.priority,
                  window: values.window,
                  armed: values.download != null || values.upload != null,
                }
              : a,
          ),
        }));
        return;
      }
      await setRuleApi(ruleParams(values, app.ruleKey));
      refetch();
    },
    [refetch],
  );

  const createRule = useCallback(
    async (values: RuleValues) => {
      if (isMock) {
        mutate((model) => ({
          ...model,
          apps: [...model.apps.filter((a) => a.key !== values.name), mockRow(values)],
          totalRules: model.totalRules + 1,
        }));
        return;
      }
      await setRuleApi(ruleParams(values));
      refetch();
    },
    [refetch],
  );

  const removeRule = useCallback(
    async (app: AppRow) => {
      if (isMock) {
        mutate((model) => ({
          ...model,
          apps: model.apps.map((a) =>
            a.key === app.key ? { ...a, unattributed: true, armed: false, ruleKey: undefined } : a,
          ),
        }));
        return;
      }
      // Without a key there is nothing to delete; say so instead of claiming
      // success and letting the next poll undo it.
      if (!app.ruleKey) throw new Error("no stored rule for this row");
      await removeRuleApi(app.ruleKey);
      disarmedRef.current.delete(app.key);
      refetch();
    },
    [refetch],
  );

  const resetStats = useCallback(async () => {
    historyRef.current = [];
    if (isMock) {
      mutate((model) => ({ ...model, history: [] }));
      return;
    }
    await resetStatsApi();
    refetch();
  }, [refetch]);

  const changeUnit = useCallback(
    (unit: Unit) => {
      if (isMock) {
        mutate((model) => ({ ...model, unit }));
        return;
      }
      setUnitApi(unit)
        .then(refetch)
        .catch((error) => onError?.(`Display unit: ${String(error)}`));
    },
    [refetch, onError],
  );

  const saveGlobal = useCallback(
    async (values: GlobalValues) => {
      if (isMock) {
        mutate((model) => ({
          ...model,
          enabled: values.enabled,
          globalDownLimit: values.download,
          globalUpLimit: values.upload,
          globalDownMinimum: values.downloadMinimum,
          globalUpMinimum: values.uploadMinimum,
          globalPriority: values.downloadPriority,
          globalUpPriority: values.uploadPriority,
        }));
        return;
      }
      await setGlobalApi({
        enabled: values.enabled,
        download_limit: values.download,
        upload_limit: values.upload,
        download_minimum: values.downloadMinimum,
        upload_minimum: values.uploadMinimum,
        download_priority: values.downloadPriority,
        upload_priority: values.uploadPriority,
      });
      refetch();
    },
    [refetch],
  );

  /**
   * Arm/disarm a rule. Disarming clears its limits on the daemon (remembering
   * them for this session); arming sends them back.
   */
  const toggleArm = useCallback(
    (key: string, armed: boolean) => {
      const app = viewRef.current.model?.apps.find((a) => a.key === key);
      if (!app || !app.ruleKey) return;
      const current = { download: app.downloadLimit, upload: app.uploadLimit };
      let next: { download: number | null; upload: number | null };

      if (armed) {
        next = disarmedRef.current.get(key) ?? current;
        disarmedRef.current.delete(key);
      } else {
        if (current.download != null || current.upload != null) {
          disarmedRef.current.set(key, current);
        }
        next = { download: null, upload: null };
      }

      if (isMock) {
        mutate((model) => ({
          ...model,
          apps: model.apps.map((a) =>
            a.key === key
              ? { ...a, downloadLimit: next.download, uploadLimit: next.upload, armed }
              : a,
          ),
        }));
        return;
      }
      const values = valuesFromApp(app, next.download, next.upload);
      setRuleApi(ruleParams(values, app.ruleKey))
        .then(refetch)
        .catch((error) => onError?.(`Limit for ${app.name}: ${String(error)}`));
    },
    [refetch, onError],
  );

  return {
    ...view,
    toggle,
    toggleArm,
    setApp,
    createRule,
    removeRule,
    resetStats,
    changeUnit,
    saveGlobal,
    refresh: refetch,
    sortKey,
    setSort,
  };
}
