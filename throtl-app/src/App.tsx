import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BudgetsSheet } from "./components/BudgetsSheet";
import { CommandPalette } from "./components/CommandPalette";
import { GlobalSheet } from "./components/GlobalSheet";
import { Header } from "./components/Header";
import { Overview } from "./components/Overview";
import { ProcessList } from "./components/ProcessList";
import { ProfileMenu } from "./components/ProfileMenu";
import { RuleSheet } from "./components/RuleSheet";
import { ScheduleSheet } from "./components/ScheduleSheet";
import { SettingsSheet, type Density, type Theme } from "./components/SettingsSheet";
import { StatisticsSheet } from "./components/StatisticsSheet";
import { UpdateBanner } from "./components/UpdateBanner";
import { ConnectingState, DeniedState, OfflineState } from "./components/States";
import { ToastHost, useToasts } from "./components/Toast";
import { Toolbar } from "./components/Toolbar";
import { Tour } from "./components/Tour";
import { Wizard } from "./components/Wizard";
import { useDaemon } from "./hooks/useDaemon";
import { useBudgetAlerts } from "./hooks/useBudgetAlerts";
import { useUpdateCheck } from "./hooks/useUpdateCheck";
import { getConfig, importConfig, activateProfile, saveProfile as saveProfileApi } from "./lib/api";
import { isMock, isTauri, runSetup } from "./lib/ipc";
import type { AppRow, GlobalValues } from "./lib/model";

// Fallback for the browser; inside Tauri the real bundle version wins.
const APP_VERSION_FALLBACK = "0.14.0";
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
  const { toasts, push } = useToasts();
  const onDaemonError = useCallback(
    (message: string) => push(message, "error"),
    [push],
  );
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
  } = useDaemon({ onError: onDaemonError });
  const [query, setQuery] = useState(
    () => new URLSearchParams(location.search).get("q") ?? "",
  );
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
  const [version, setVersion] = useState(APP_VERSION_FALLBACK);
  const [scrolled, setScrolled] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const updates = useUpdateCheck(version);
  useBudgetAlerts(Boolean(model));

  useEffect(() => {
    if (!isTauri()) return;
    void import("@tauri-apps/api/app")
      .then((mod) => mod.getVersion())
      .then(setVersion)
      .catch(() => {});
  }, []);

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
        setPaletteOpen((v) => !v);
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

  // First run: offer the guided tour once the dashboard has data — but never
  // during screenshot/deep-link runs, which pin a deterministic view.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (
      params.has("static") ||
      params.has("sheet") ||
      params.has("mockstate") ||
      params.has("menu") ||
      params.has("pop")
    )
      return;
    if (!model || localStorage.getItem("throtl-tour-seen")) return;
    localStorage.setItem("throtl-tour-seen", "1");
    setTourOpen(true);
  }, [model]);

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

  const closeTour = useCallback(() => setTourOpen(false), []);

  const openWizard = useCallback(() => {
    setTourOpen(false);
    setProfileOpen(false);
    setWizardOpen(true);
  }, []);

  const saveProfile = useCallback(
    async (name: string) => {
      await saveProfileApi(name);
      refresh();
    },
    [refresh],
  );

  const handleSaveGlobal = useCallback(
    async (values: GlobalValues) => {
      await saveGlobal(values);
    },
    [saveGlobal],
  );

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

  // Stable handlers for the process rows (they are memoised against identity).
  const handleRemove = useCallback(
    async (app: AppRow) => {
      try {
        await removeRule(app);
        push(`Rule removed for ${app.name}`);
      } catch (err) {
        push(`Could not remove the rule: ${String(err)}`, "error");
      }
    },
    [removeRule, push],
  );

  const resetCounters = useCallback(async () => {
    try {
      await resetStats();
      push("Counters reset");
    } catch (err) {
      push(`Could not reset the counters: ${String(err)}`, "error");
    }
  }, [resetStats, push]);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => (resolveTheme(prev) === "light" ? "dark" : "light"));
  }, []);

  const switchProfile = useCallback(
    async (name: string) => {
      try {
        await activateProfile(name);
        push(`Profile “${name}” activated`);
        refresh();
      } catch (err) {
        push(`Could not switch profile: ${String(err)}`, "error");
      }
    },
    [push, refresh],
  );

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
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Header
        enabled={model?.enabled ?? false}
        profile={model?.profile ?? "—"}
        history={model?.history ?? []}
        onToggle={toggle}
        onSettings={() => setSheet("settings")}
        onStats={() => setSheet("stats")}
        onBudgets={() => setSheet("budgets")}
        profileOpen={profileOpen}
        scrolled={scrolled}
        onTour={() => setTourOpen(true)}
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

      <main className="app-main" id="main-content" tabIndex={-1}>
        {updates.latest && (
          <UpdateBanner
            version={updates.latest}
            current={version}
            onDismiss={updates.dismiss}
          />
        )}
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
                onRemove={handleRemove}
                onAddRule={openCreate}
                onReset={resetCounters}
                onToast={push}
                query={query}
                onClearQuery={() => setQuery("")}
                onSetup={() => setWizardOpen(true)}
                groupsVisible={apps.length}
                groupsTotal={model.groupsTotal}
              />
            </section>

            <div className="app-footer">
              Press <span className="kbd">L</span> for light/dark ·{" "}
              <span className="kbd">Ctrl/⌘ K</span> for commands.
            </div>
          </>
        ) : state === "denied" ? (
          <DeniedState
            onRetry={() => location.reload()}
            onSetup={() => {
              runSetup()
                .then(() => push("Administrator authentication requested — check the prompt"))
                .catch((err) => push(`Setup failed: ${String(err)}`, "error"));
            }}
          />
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
          version={version}
          onExport={() => void exportConfig()}
          onImport={(file) => void importConfigFile(file)}
          checkUpdates={updates.enabled}
          onCheckUpdates={updates.setEnabled}
          onCheckNow={() => {
            void updates
              .check()
              .then((found) =>
                push(found ? `Throtl ${found} is available` : "You are up to date"),
              );
          }}
          onClose={closeSheets}
        />
      )}
      {sheet === "stats" && <StatisticsSheet onClose={closeSheets} />}
      {sheet === "budgets" && <BudgetsSheet onClose={closeSheets} onToast={push} />}
      {sheet === "globals" && model && (
        <GlobalSheet
          model={model}
          onClose={closeSheets}
          onSave={async (values) => {
            try {
              await saveGlobal(values);
              push("Global limits saved");
              closeSheets();
            } catch (err) {
              push(`Could not save the limits: ${String(err)}`, "error");
            }
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
          onSave={async (values) => {
            try {
              await setApp(editApp, values);
              push(`Rule saved for ${editApp.name}`);
              closeSheets();
            } catch (err) {
              push(`Could not save the rule: ${String(err)}`, "error");
            }
          }}
          onDelete={async () => {
            try {
              await removeRule(editApp);
              push(`Rule removed for ${editApp.name}`);
              closeSheets();
            } catch (err) {
              push(`Could not remove the rule: ${String(err)}`, "error");
            }
          }}
        />
      )}
      {createOpen && model && (
        <RuleSheet
          mode="create"
          app={createApp ?? undefined}
          unit={model.unit}
          onClose={closeSheets}
          onSave={async (values) => {
            try {
              await createRule(values);
              push(`Rule created for ${values.name}`);
              closeSheets();
            } catch (err) {
              push(`Could not create the rule: ${String(err)}`, "error");
            }
          }}
        />
      )}

      <ToastHost toasts={toasts} />
      {tourOpen && <Tour onClose={closeTour} onSetup={openWizard} />}
      {wizardOpen && model && (
        <Wizard
          model={model}
          onClose={() => setWizardOpen(false)}
          onToast={push}
          onSaveProfile={saveProfile}
          onSaveGlobal={handleSaveGlobal}
          onCreateRule={createRule}
        />
      )}
      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          enabled={model?.enabled ?? false}
          onToggleShaping={() => toggle(!(model?.enabled ?? false))}
          onFocusFilter={() => searchRef.current?.focus()}
          onAddRule={() => openCreate()}
          onOpenSheet={(kind) => {
            if (kind === "globals") setSheet("globals");
            else if (kind === "stats") setSheet("stats");
            else if (kind === "budgets") setSheet("budgets");
            else setSheet("settings");
          }}
          onTour={() => setTourOpen(true)}
          onResetStats={() => void resetCounters()}
          onToggleTheme={toggleTheme}
          onSwitchProfile={(name) => void switchProfile(name)}
        />
      )}
    </div>
  );
}
