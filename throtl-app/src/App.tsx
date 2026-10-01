import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BudgetsSheet } from "./components/BudgetsSheet";
import { GlobalSheet } from "./components/GlobalSheet";
import { Header } from "./components/Header";
import { Overview } from "./components/Overview";
import { ProcessList } from "./components/ProcessList";
import { ProfileMenu } from "./components/ProfileMenu";
import { RuleSheet } from "./components/RuleSheet";
import { ScheduleSheet } from "./components/ScheduleSheet";
import { SettingsSheet, type Density, type Theme } from "./components/SettingsSheet";
import { StatisticsSheet } from "./components/StatisticsSheet";
import { ConnectingState, DeniedState, OfflineState } from "./components/States";
import { ToastHost, useToasts } from "./components/Toast";
import { Toolbar } from "./components/Toolbar";
import { useDaemon } from "./hooks/useDaemon";
import { getConfig, importConfig } from "./lib/api";
import { isMock } from "./lib/ipc";
import type { AppRow } from "./lib/model";

const APP_VERSION = "0.1.0";
const SOCKET_PATH = "/run/throtl/daemon.sock";

type SheetKind = "settings" | "stats" | "budgets" | "globals" | "schedule" | null;

const SHEET_NAMES: SheetKind[] = ["settings", "stats", "budgets", "globals", "schedule"];

function resolveTheme(theme: Theme): "dark" | "light" {
  if (theme !== "system") return theme;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function storedTheme(): Theme {
  // `?theme=` wins so screenshot runs and deep links are deterministic and
  // agree with the pre-paint resolution in index.html.
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "light" || q === "dark" || q === "system") return q;
  const raw = localStorage.getItem("throtl-theme");
  return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
}

export function App() {
  const {
    model,
    state,
    error,
    toggle,
    toggleArm,
    setApp,
    createRule,
    removeRule,
    resetStats,
    changeUnit,
    saveGlobal,
    refresh,
    sortKey,
    setSort,
  } = useDaemon();
  const [query, setQuery] = useState("");
  const [sheet, setSheet] = useState<SheetKind>(() => {
    const s = new URLSearchParams(location.search).get("sheet") as SheetKind;
    return SHEET_NAMES.includes(s) ? s : null;
  });
  const [editApp, setEditApp] = useState<AppRow | null>(null);
  const [createApp, setCreateApp] = useState<AppRow | null>(null);
  const [createOpen, setCreateOpen] = useState(
    () => new URLSearchParams(location.search).get("sheet") === "new",
  );
  const [profileOpen, setProfileOpen] = useState(
    () => new URLSearchParams(location.search).get("pop") === "profile",
  );
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
        setTheme((prev) => (resolveTheme(prev) === "light" ? "dark" : "light"));
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") setProfileOpen(false);
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
    setCreateOpen(false);
    setCreateApp(null);
  }, []);

  const openCreate = useCallback((app?: AppRow) => {
    setCreateApp(app ?? null);
    setCreateOpen(true);
  }, []);

  const exportConfig = useCallback(async () => {
    if (isMock) {
      push("Export works in the desktop app", "error");
      return;
    }
    try {
      const config = await getConfig();
      const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "throtl-config.json";
      link.click();
      URL.revokeObjectURL(url);
      push("Configuration exported");
    } catch (err) {
      push(`Export failed: ${String(err)}`, "error");
    }
  }, [push]);

  const importConfigFile = useCallback(
    async (file: File) => {
      if (isMock) {
        push("Import works in the desktop app", "error");
        return;
      }
      try {
        const data = JSON.parse(await file.text());
        await importConfig(data);
        push("Configuration imported");
        refresh();
      } catch (err) {
        push(`Import failed: ${String(err)}`, "error");
      }
    },
    [push, refresh],
  );

  return (
    <div className="app-shell">
      <Header
        enabled={model?.enabled ?? false}
        profile={model?.profile ?? "—"}
        onToggle={toggle}
        onSettings={() => setSheet("settings")}
        onStats={() => setSheet("stats")}
        onBudgets={() => setSheet("budgets")}
        profileOpen={profileOpen}
        onProfileToggle={() => setProfileOpen((v) => !v)}
        profiles={
          <ProfileMenu
            onClose={() => setProfileOpen(false)}
            onToast={push}
            onChanged={refresh}
            onSchedules={() => setSheet("schedule")}
          />
        }
      />

      <main className="app-main">
        {model ? (
          <>
            <Overview
              model={model}
              peakApp={model.topTalkers[0]?.name ?? "—"}
              onGlobals={() => setSheet("globals")}
            />

            <section className="card applications">
              <Toolbar
                query={query}
                onQuery={setQuery}
                sortKey={sortKey}
                onSort={setSort}
                onAddRule={() => openCreate()}
                inputRef={searchRef}
              />
              <ProcessList
                apps={apps}
                unit={model.unit}
                sortKey={sortKey}
                onToggleArm={toggleArm}
                onEdit={setEditApp}
                onRemove={(app) => {
                  removeRule(app);
                  push(`Rule removed for ${app.name}`);
                }}
                onAddRule={openCreate}
                onReset={() => {
                  resetStats();
                  push("Counters reset");
                }}
                onToast={push}
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
          onUnit={changeUnit}
          socket={SOCKET_PATH}
          state={state}
          version={APP_VERSION}
          onExport={() => void exportConfig()}
          onImport={(file) => void importConfigFile(file)}
          onClose={closeSheets}
        />
      )}
      {sheet === "stats" && <StatisticsSheet onClose={closeSheets} />}
      {sheet === "budgets" && <BudgetsSheet onClose={closeSheets} onToast={push} />}
      {sheet === "globals" && model && (
        <GlobalSheet
          model={model}
          onClose={closeSheets}
          onSave={(values) => {
            saveGlobal(values);
            push("Global limits saved");
            closeSheets();
          }}
        />
      )}
      {sheet === "schedule" && (
        <ScheduleSheet onClose={closeSheets} onToast={push} onChanged={refresh} />
      )}
      {editApp && model && (
        <RuleSheet
          mode="edit"
          app={editApp}
          unit={model.unit}
          onClose={closeSheets}
          onSave={(values) => {
            setApp(editApp, values);
            push(`Rule saved for ${editApp.name}`);
            closeSheets();
          }}
          onDelete={() => {
            removeRule(editApp);
            push(`Rule removed for ${editApp.name}`);
            closeSheets();
          }}
        />
      )}
      {createOpen && model && (
        <RuleSheet
          mode="create"
          app={createApp ?? undefined}
          unit={model.unit}
          onClose={closeSheets}
          onSave={(values) => {
            createRule(values);
            push(`Rule created for ${values.name}`);
            closeSheets();
          }}
        />
      )}

      <ToastHost toasts={toasts} />
    </div>
  );
}
