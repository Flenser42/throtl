import { useEffect, useMemo, useRef, useState } from "react";

import { GlobalsTile } from "./components/GlobalsTile";
import { Header } from "./components/Header";
import { LiveGraph } from "./components/LiveGraph";
import { ProcessList } from "./components/ProcessList";
import { StatTile } from "./components/StatTile";
import { Toolbar } from "./components/Toolbar";
import { TopTalkers } from "./components/TopTalkers";
import { AnimatedNumber } from "./components/AnimatedNumber";
import { useDaemon } from "./hooks/useDaemon";
import { splitRate } from "./lib/format";

type Theme = "dark" | "light";

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function App() {
  const { model, state, error, toggle, sortKey, setSort } = useDaemon();
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.key.toLowerCase() === "l" &&
        !event.metaKey &&
        !event.ctrlKey &&
        !event.altKey
      ) {
        const next: Theme = currentTheme() === "light" ? "dark" : "light";
        document.documentElement.dataset.theme = next;
        try {
          localStorage.setItem("throtl-theme", next);
        } catch {
          /* ignore */
        }
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const apps = useMemo(() => {
    if (!model) return [];
    const q = query.trim().toLowerCase();
    return q ? model.apps.filter((a) => a.name.toLowerCase().includes(q)) : model.apps;
  }, [model, query]);

  if (!model) {
    return (
      <div className="app-shell">
        <AppHeaderFallback />
        <main className="app-main">
          <div className="card" style={{ padding: 32, textAlign: "center" }}>
            <div className="card-title">
              {state === "connecting" ? "Connecting to the daemon…" : "Daemon not reachable"}
            </div>
            <div className="card-sub" style={{ marginTop: 8 }}>
              {error ?? "The Throtl service runs in the background and needs root."}
            </div>
          </div>
        </main>
      </div>
    );
  }

  const down = splitRate(model.downKbit, model.unit, 1);
  const up = splitRate(model.upKbit, model.unit, 2);
  const limitedCount = model.apps.filter((a) => !a.unattributed && a.armed).length;
  const peakApp = model.topTalkers[0]?.name ?? "—";

  return (
    <div className="app-shell">
      <Header
        enabled={model.enabled}
        profile={model.profile}
        onToggle={toggle}
        onSettings={() => {}}
        onStats={() => {}}
      />

      <main className="app-main">
        <div className="grid-4">
          <StatTile
            label="Download"
            value={<AnimatedNumber value={model.downKbit} format={(n) => splitRate(n, model.unit, 1).value} />}
            unit={down.unit}
            valueClass="t-down"
            trend={`▾ ${model.trendDownPct}%`}
            caption={`matched to ${model.matchedApps} apps`}
            spark={model.history.map((d) => d.down)}
            sparkColor="var(--down)"
          />
          <StatTile
            label="Upload"
            value={<AnimatedNumber value={model.upKbit} format={(n) => splitRate(n, model.unit, 2).value} />}
            unit={up.unit}
            valueClass="t-up"
            trend={`▴ ${model.trendUpPct}%`}
            caption={`${limitedCount} apps limited`}
            spark={model.history.map((d) => d.up)}
            sparkColor="var(--up)"
          />
          <StatTile
            label="Active rules"
            value={`${model.activeRules}`}
            unit={`of ${model.totalRules}`}
            valueClass="t-acc"
            trend={`${model.scheduledRules} scheduled`}
            caption="time-windowed"
            bars
          />
          <GlobalsTile
            unit={model.unit}
            downLimit={model.globalDownLimit}
            upLimit={model.globalUpLimit}
            priority={model.globalPriority}
            profile={model.profile}
          />
        </div>

        <div className="grid-main">
          <LiveGraph
            history={model.history}
            matchedApps={model.matchedApps}
            windowSumBytes={model.windowSumBytes}
            peakApp={peakApp}
          />
          <TopTalkers talkers={model.topTalkers} windowLabel="1 min" />
        </div>

        <div className="card">
          <Toolbar
            query={query}
            onQuery={setQuery}
            sortKey={sortKey}
            onSort={setSort}
            onAddRule={() => {}}
            inputRef={searchRef}
          />
          <ProcessList
            apps={apps}
            unit={model.unit}
            sortKey={sortKey}
            groupsVisible={apps.length}
            groupsTotal={model.groupsTotal}
          />
        </div>

        <div className="hint">
          Rates are 1-minute rolling averages. · Press <span className="kbd">L</span> for light/dark.
        </div>
      </main>
    </div>
  );
}

function AppHeaderFallback() {
  return (
    <Header
      enabled={false}
      profile="—"
      onToggle={() => {}}
      onSettings={() => {}}
      onStats={() => {}}
    />
  );
}
