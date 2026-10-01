import { useEffect, useRef, useState } from "react";

import {
  removeRule as removeRuleApi,
  resetStats as resetStatsApi,
  setRule as setRuleApi,
  setUnit as setUnitApi,
} from "../lib/api";
import { buildModel } from "../lib/buildModel";
import { isMock, listen, invokeDaemon } from "../lib/ipc";
import { gradientFor, initialFor, mockModel, tickModel } from "../lib/mock";
import type { AppRow, DaemonView, HistoryPoint, RuleValues } from "../lib/model";
import type { Budgets, Config, ProcessState, Unit } from "../lib/types";

// 30 minutes at 1 Hz covers the "15m"/"all" graph windows.
const MAX_HISTORY = 1800;

type SortKey = "download" | "upload" | "name";

function ruleParams(values: RuleValues): Record<string, unknown> {
  return {
    name: values.name,
    match_type: values.matchType,
    match_value: values.matchValue,
    download_limit: values.download,
    upload_limit: values.upload,
    priority: values.priority,
    window: values.window ?? undefined,
  };
}

function mockRow(values: RuleValues): AppRow {
  return {
    key: values.name,
    name: values.name,
    initial: initialFor(values.name),
    gradient: gradientFor(values.name),
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
    spark: [],
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
 */
export function useDaemon(): DaemonView & {
  toggle: (enabled: boolean) => void;
  toggleArm: (key: string, armed: boolean) => void;
  setApp: (app: AppRow, values: RuleValues) => void;
  createRule: (values: RuleValues) => void;
  removeRule: (app: AppRow) => void;
  resetStats: () => void;
  changeUnit: (unit: Unit) => void;
  refresh: () => void;
  sortKey: SortKey;
  setSort: (key: SortKey) => void;
} {
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
  const historyRef = useRef<HistoryPoint[]>(isMock ? mockModel().history.slice(-MAX_HISTORY) : []);
  const realRef = useRef<{ state?: ProcessState; config?: Config; budgets?: Budgets }>({});
  const refreshRef = useRef<() => void>(() => {});

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
          setView((prev) => ({ ...prev, state: state as DaemonView["state"] }));
        }
      });
      if (disposed) {
        offUpdate();
        offState();
        return;
      }
      unlisteners.push(offUpdate, offState);
      refreshRef.current = () => {
        void fetchAll();
      };
      void fetchAll();
    })();

    return () => {
      disposed = true;
      unlisteners.forEach((off) => off());
    };
  }, []);

  const toggle = (enabled: boolean) => {
    setView((prev) => (prev.model ? { ...prev, model: { ...prev.model, enabled } } : prev));
    if (!isMock) void invokeDaemon("toggle", { enabled }).catch(() => {});
  };

  const setApp = (app: AppRow, values: RuleValues) => {
    setView((prev) =>
      prev.model
        ? {
            ...prev,
            model: {
              ...prev.model,
              apps: prev.model.apps.map((a) =>
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
            },
          }
        : prev,
    );
    if (!isMock) void setRuleApi(ruleParams(values)).catch(() => {});
  };

  const createRule = (values: RuleValues) => {
    setView((prev) =>
      prev.model
        ? {
            ...prev,
            model: {
              ...prev.model,
              apps: [...prev.model.apps.filter((a) => a.key !== values.name), mockRow(values)],
              totalRules: prev.model.totalRules + 1,
              groupsTotal: prev.model.groupsTotal + 1,
            },
          }
        : prev,
    );
    if (!isMock) void setRuleApi(ruleParams(values)).catch(() => {});
  };

  const removeRule = (app: AppRow) => {
    if (!isMock && app.ruleKey) void removeRuleApi(app.ruleKey).catch(() => {});
    setView((prev) =>
      prev.model
        ? {
            ...prev,
            model: {
              ...prev.model,
              apps: prev.model.apps.map((a) =>
                a.key === app.key
                  ? {
                      ...a,
                      unattributed: true,
                      armed: false,
                      downloadLimit: null,
                      uploadLimit: null,
                      priority: "normal",
                      windowLabel: null,
                      windowActive: false,
                      windowState: null,
                      ruleKey: undefined,
                      meta: `${a.meta.split(" · ")[0]} · no rule applies`,
                    }
                  : a,
              ),
            },
          }
        : prev,
    );
  };

  const resetStats = () => {
    if (!isMock) void resetStatsApi().catch(() => {});
    historyRef.current = [];
    setView((prev) =>
      prev.model ? { ...prev, model: { ...prev.model, history: [], windowSumBytes: 0 } } : prev,
    );
  };

  const changeUnit = (unit: Unit) => {
    if (!isMock) void setUnitApi(unit).catch(() => {});
    setView((prev) => (prev.model ? { ...prev, model: { ...prev.model, unit } } : prev));
  };

  const toggleArm = (key: string, armed: boolean) => {
    setView((prev) =>
      prev.model
        ? {
            ...prev,
            model: {
              ...prev.model,
              apps: prev.model.apps.map((a) => (a.key === key ? { ...a, armed } : a)),
            },
          }
        : prev,
    );
  };

  return {
    ...view,
    toggle,
    toggleArm,
    setApp,
    createRule,
    removeRule,
    resetStats,
    changeUnit,
    refresh: () => refreshRef.current(),
    sortKey,
    setSort,
  };
}
