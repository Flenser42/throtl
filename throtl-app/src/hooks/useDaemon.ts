import { useEffect, useRef, useState } from "react";

import { buildModel } from "../lib/buildModel";
import { isMock, listen, invokeDaemon } from "../lib/ipc";
import { mockModel, tickModel } from "../lib/mock";
import type { DaemonView, HistoryPoint } from "../lib/model";
import type { Budgets, Config, ProcessState } from "../lib/types";

const MAX_HISTORY = 300;

/**
 * Owns the daemon connection. In the browser (no Tauri, or VITE_MOCK=1) it
 * drives a deterministic mock so the UI can be developed and screenshotted.
 * Inside Tauri it subscribes to the Rust bridge events and invokes commands.
 */
export function useDaemon(): DaemonView & {
  toggle: (enabled: boolean) => void;
  setSort: (key: "download" | "upload" | "name") => void;
  sortKey: "download" | "upload" | "name";
} {
  const [view, setView] = useState<DaemonView>({
    state: isMock ? "connected" : "connecting",
    error: null,
    model: isMock ? mockModel() : null,
  });
  const [sortKey, setSort] = useState<"download" | "upload" | "name">("download");
  const historyRef = useRef<HistoryPoint[]>(isMock ? mockModel().history.slice(-MAX_HISTORY) : []);
  const realRef = useRef<{
    state?: ProcessState;
    config?: Config;
    budgets?: Budgets;
  }>({});

  // ---- mock mode ----
  useEffect(() => {
    if (!isMock) return;
    // `?static` freezes the mock so screenshots match the mockup exactly.
    if (new URLSearchParams(location.search).has("static")) return;
    let tick = 0;
    const id = window.setInterval(() => {
      tick += 1;
      setView((prev) =>
        prev.model ? { ...prev, model: tickModel(prev.model, tick) } : prev,
      );
    }, 1000);
    return () => window.clearInterval(id);
  }, []);

  // ---- real (Tauri) mode ----
  useEffect(() => {
    if (isMock) return;
    let disposed = false;
    const unlisteners: Array<() => void> = [];

    const rebuild = () => {
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

    (async () => {
      unlisteners.push(
        await listen<ProcessState>("daemon:update", (state) => {
          realRef.current.state = state;
          const point: HistoryPoint = {
            t: Date.now() / 1000,
            down: state.global.download ?? 0,
            up: state.global.upload ?? 0,
          };
          historyRef.current = [...historyRef.current.slice(-(MAX_HISTORY - 1)), point];
          rebuild();
        }),
      );
      unlisteners.push(
        await listen<string>("daemon:state", (state) => {
          if (state === "offline" || state === "denied") {
            setView((prev) => ({ ...prev, state, error: prev.error }));
          }
        }),
      );
      try {
        const [status, config, budgets] = await Promise.all([
          invokeDaemon<{ active_profile?: string }>("get_status"),
          invokeDaemon<Config>("get_config"),
          invokeDaemon<Budgets>("get_budgets"),
        ]);
        if (disposed) return;
        realRef.current.config = config;
        realRef.current.budgets = budgets;
        void status;
        const state = await invokeDaemon<ProcessState>("list_processes");
        realRef.current.state = state;
        rebuild();
      } catch (error) {
        if (!disposed) setView({ state: "offline", error: String(error), model: null });
      }
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

  return { ...view, toggle, sortKey, setSort };
}
