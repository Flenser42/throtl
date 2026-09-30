import { useEffect, useRef, useState } from "react";

import { buildModel } from "../lib/buildModel";
import { isMock, listen, invokeDaemon } from "../lib/ipc";
import { mockModel, tickModel } from "../lib/mock";
import type { DaemonView, HistoryPoint } from "../lib/model";
import type { Budgets, Config, ProcessState } from "../lib/types";

// 30 minutes at 1 Hz covers the "15m"/"all" graph windows.
const MAX_HISTORY = 1800;

type SortKey = "download" | "upload" | "name";

/**
 * Owns the daemon connection. In the browser (no Tauri, or VITE_MOCK=1) it
 * drives a deterministic mock so the UI can be developed and screenshotted.
 * Inside Tauri it subscribes to the Rust bridge events and invokes commands.
 */
export function useDaemon(): DaemonView & {
  toggle: (enabled: boolean) => void;
  toggleArm: (key: string, armed: boolean) => void;
  sortKey: SortKey;
  setSort: (key: SortKey) => void;
} {
  const [view, setView] = useState<DaemonView>({
    state: isMock ? "connected" : "connecting",
    error: null,
    model: isMock ? mockModel() : null,
  });
  const [sortKey, setSort] = useState<SortKey>("download");
  const historyRef = useRef<HistoryPoint[]>(isMock ? mockModel().history.slice(-MAX_HISTORY) : []);
  const realRef = useRef<{ state?: ProcessState; config?: Config; budgets?: Budgets }>({});

  // ---- mock mode ----
  useEffect(() => {
    if (!isMock) return;
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
    // Note: arming/disarming a per-app rule is Phase 3 (needs set_process).
  };

  return { ...view, toggle, toggleArm, sortKey, setSort };
}
