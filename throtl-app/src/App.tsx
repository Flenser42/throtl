import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BudgetsSheet } from "./components/BudgetsSheet";
import { Header } from "./components/Header";
import { Overview } from "./components/Overview";
import { ProcessList } from "./components/ProcessList";
import { RuleSheet } from "./components/RuleSheet";
import { SettingsSheet, type Density, type Theme } from "./components/SettingsSheet";
import { StatisticsSheet } from "./components/StatisticsSheet";
import { ConnectingState, DeniedState, OfflineState } from "./components/States";
import { ToastHost, useToasts } from "./components/Toast";
import { Toolbar } from "./components/Toolbar";
import { useDaemon } from "./hooks/useDaemon";
import type { AppRow } from "./lib/model";

const APP_VERSION = "0.1.0";
const SOCKET_PATH = "/run/throtl/daemon.sock";

type SheetKind = "settings" | "stats" | "budgets" | null;

function resolveTheme(theme: Theme): "dark" | "light" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function storedTheme(): Theme {
  const raw = localStorage.getItem("throtl-theme");
  return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
}

export function App() {
  const { model, state, error, toggle, toggleArm, setApp, sortKey, setSort } = useDaemon();
  const [query, setQuery] = useState("");
  const [sheet, setSheet] = useState<SheetKind>(() => {
    const s = new URLSearchParams(location.search).get("sheet");
    return s === "settings" || s === "stats" || s === "budgets" ? s : null;
  });
  const [editApp, setEditApp] = useState<AppRow | null>(null);
  const [theme, setTheme] = useState<Theme>(storedTheme);
  const [density, setDensity] = useState<Density>(
    (localStorage.getItem("throtl-density") as Density) || "comfortable",
  );
  const { toasts, push } = useToasts();
  const searchRef = useRef<HTMLInputElement>(null);

  // Theme: apply and follow the system when "system" is chosen.
  useEffect(() => {
    const apply = () => {
      document.documentElement.dataset.theme = resolveTheme(theme);
    };
    apply();
    localStorage.setItem("throtl-theme", theme);
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [theme]);

  useEffect(() => {
    document.documentElement.dataset.density = density;
    localStorage.setItem("throtl-density", density);
  }, [density]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        !!target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (!typing && event.key.toLowerCase() === "l" && !event.metaKey && !event.ctrlKey) {
        setTheme((prev) =>
          resolveTheme(prev) === "light" ? "dark" : "light",
        );
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // `?sheet=rule` opens the rule editor for the first app (screenshots).
  useEffect(() => {
    if (!model || editApp) return;
    if (new URLSearchParams(location.search).get("sheet") === "rule") {
      setEditApp(model.apps.find((a) => !a.unattributed) ?? model.apps[0]);
    }
  }, [model, editApp]);

  const apps = useMemo(() => {
    if (!model) return [];
    const q = query.trim().toLowerCase();
    return q ? model.apps.filter((a) => a.name.toLowerCase().includes(q)) : model.apps;
  }, [model, query]);

  const closeSheets = useCallback(() => {
    setSheet(null);
    setEditApp(null);
  }, []);

  return (
    <div className="app-shell">
      <Header
        enabled={model?.enabled ?? false}
        profile={model?.profile ?? "—"}
        onToggle={toggle}
        onSettings={() => setSheet("settings")}
        onStats={() => setSheet("stats")}
        onBudgets={() => setSheet("budgets")}
      />

      <main className="app-main">
        {model ? (
          <>
            <Overview model={model} peakApp={model.topTalkers[0]?.name ?? "—"} />

            <section className="card applications">
              <Toolbar
                query={query}
                onQuery={setQuery}
                sortKey={sortKey}
                onSort={setSort}
                onAddRule={() => push("Add rule — coming in the next step")}
                inputRef={searchRef}
              />
              <ProcessList
                apps={apps}
                unit={model.unit}
                sortKey={sortKey}
                onToggleArm={toggleArm}
                onEdit={setEditApp}
                groupsVisible={apps.length}
                groupsTotal={model.groupsTotal}
              />
            </section>

            <div className="hint">
              Press <span className="kbd">L</span> for light/dark ·{" "}
              <span className="kbd">Ctrl/⌘ K</span> to filter.
            </div>
          </>
        ) : state === "denied" ? (
          <DeniedState onRetry={() => location.reload()} />
        ) : state === "offline" ? (
          <OfflineState message={error} onRetry={() => location.reload()} />
        ) : (
          <ConnectingState />
        )}
      </main>

      {sheet === "settings" && (
        <SettingsSheet
          theme={theme}
          onTheme={setTheme}
          density={density}
          onDensity={setDensity}
          unit={model?.unit ?? "mBs"}
          socket={SOCKET_PATH}
          state={state}
          version={APP_VERSION}
          onClose={closeSheets}
        />
      )}
      {sheet === "stats" && <StatisticsSheet onClose={closeSheets} />}
      {sheet === "budgets" && <BudgetsSheet onClose={closeSheets} onToast={push} />}
      {editApp && model && (
        <RuleSheet
          app={editApp}
          unit={model.unit}
          onClose={closeSheets}
          onSave={(app, values) => {
            setApp(app, values);
            push(`Rule saved for ${app.name}`);
            closeSheets();
          }}
        />
      )}

      <ToastHost toasts={toasts} />
    </div>
  );
}
