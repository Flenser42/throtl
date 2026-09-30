import { useEffect, useMemo, useRef, useState } from "react";

import { Header } from "./components/Header";
import { Overview } from "./components/Overview";
import { ProcessList } from "./components/ProcessList";
import { Toolbar } from "./components/Toolbar";
import { useDaemon } from "./hooks/useDaemon";

type Theme = "dark" | "light";

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function App() {
  const { model, state, error, toggle, toggleArm, sortKey, setSort } = useDaemon();
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        !!target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable);
      if (
        !typing &&
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

  return (
    <div className="app-shell">
      <Header
        enabled={model?.enabled ?? false}
        profile={model?.profile ?? "—"}
        onToggle={toggle}
        onSettings={() => {}}
        onStats={() => {}}
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
                onAddRule={() => {}}
                inputRef={searchRef}
              />
              <ProcessList
                apps={apps}
                unit={model.unit}
                sortKey={sortKey}
                onToggleArm={toggleArm}
                groupsVisible={apps.length}
                groupsTotal={model.groupsTotal}
              />
            </section>

            <div className="hint">
              Press <span className="kbd">L</span> for light/dark ·{" "}
              <span className="kbd">Ctrl/⌘ K</span> to filter.
            </div>
          </>
        ) : (
          <section className="card" style={{ padding: 32, textAlign: "center" }}>
            <div className="card-title">
              {state === "connecting" ? "Connecting to the daemon…" : "Daemon not reachable"}
            </div>
            <div className="card-sub" style={{ marginTop: 8 }}>
              {error ?? "The Throtl service runs in the background and needs root."}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
