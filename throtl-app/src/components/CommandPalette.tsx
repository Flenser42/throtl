import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { getProfiles } from "../lib/api";
import {
  ChartLine,
  Check,
  CircleHelp,
  Gauge,
  Pause,
  Play,
  Plus,
  Refresh,
  Search,
  Settings,
  SunMoon,
  Wallet,
} from "./icons";

type SheetKind = "stats" | "budgets" | "settings" | "globals";

interface Command {
  id: string;
  label: string;
  keywords: string;
  icon: ReactNode;
  hint?: string;
  run: () => void;
}

interface Props {
  onClose: () => void;
  enabled: boolean;
  onToggleShaping: () => void;
  onFocusFilter: () => void;
  onAddRule: () => void;
  onOpenSheet: (kind: SheetKind) => void;
  onTour: () => void;
  onResetStats: () => void;
  onToggleTheme: () => void;
  onSwitchProfile: (name: string) => void;
}

/** Centered command palette: Ctrl/⌘ P, type to filter, arrows + Enter to run. */
export function CommandPalette({
  onClose,
  enabled,
  onToggleShaping,
  onFocusFilter,
  onAddRule,
  onOpenSheet,
  onTour,
  onResetStats,
  onToggleTheme,
  onSwitchProfile,
}: Props) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getProfiles()
      .then((data) => {
        if (!cancelled) {
          setProfiles(data.profiles);
          setActiveProfile(data.active);
        }
      })
      .catch(() => {
        /* profiles stay empty; the actions still work */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const close = () => onClose();

  const commands = useMemo<Command[]>(() => {
    const actions: Command[] = [
      {
        id: "filter",
        label: "Focus application filter",
        keywords: "search filter find query",
        icon: <Search />,
        hint: "⌘K",
        run: onFocusFilter,
      },
      {
        id: "rule",
        label: "Add a rule",
        keywords: "create rule limit app new",
        icon: <Plus />,
        run: onAddRule,
      },
      {
        id: "stats",
        label: "Open Statistics",
        keywords: "history chart stats",
        icon: <ChartLine />,
        run: () => onOpenSheet("stats"),
      },
      {
        id: "budgets",
        label: "Open Budgets",
        keywords: "budget volume consumption",
        icon: <Wallet />,
        run: () => onOpenSheet("budgets"),
      },
      {
        id: "globals",
        label: "Open Global limits",
        keywords: "global caps ceiling download upload",
        icon: <Gauge />,
        run: () => onOpenSheet("globals"),
      },
      {
        id: "settings",
        label: "Open Settings",
        keywords: "preferences theme unit density export import",
        icon: <Settings />,
        run: () => onOpenSheet("settings"),
      },
      {
        id: "shape",
        label: enabled ? "Pause shaping" : "Resume shaping",
        keywords: "toggle pause resume shaping limit",
        icon: enabled ? <Pause /> : <Play />,
        run: onToggleShaping,
      },
      {
        id: "theme",
        label: "Toggle light/dark theme",
        keywords: "light dark appearance",
        icon: <SunMoon />,
        hint: "L",
        run: onToggleTheme,
      },
      {
        id: "tour",
        label: "Take the guided tour",
        keywords: "help tour guide explain",
        icon: <CircleHelp />,
        run: onTour,
      },
      {
        id: "reset",
        label: "Reset counters",
        keywords: "reset clear counters zero stats",
        icon: <Refresh />,
        run: onResetStats,
      },
    ];
    const profileCommands: Command[] = profiles.map((name) => ({
      id: `profile:${name}`,
      label: `Switch profile: ${name}`,
      keywords: `profile switch ${name.toLowerCase()}`,
      icon: name === activeProfile ? <Check /> : <span className="palette-noicon" />,
      run: () => onSwitchProfile(name),
    }));
    return [...actions, ...profileCommands];
  }, [
    enabled,
    onFocusFilter,
    onAddRule,
    onOpenSheet,
    onToggleShaping,
    onToggleTheme,
    onTour,
    onResetStats,
    onSwitchProfile,
    profiles,
    activeProfile,
  ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    const matches = commands.filter(
      (c) => c.label.toLowerCase().includes(q) || c.keywords.includes(q),
    );
    const labelMatchRank = (c: Command) =>
      Number(!c.label.toLowerCase().includes(q));
    return [...matches].sort(
      (a, b) => labelMatchRank(a) - labelMatchRank(b),
    );
  }, [commands, query]);

  const hasProfiles = filtered.some((c) => c.id.startsWith("profile:"));
  const visibleActions = filtered.filter((c) => !c.id.startsWith("profile:"));
  const visibleProfiles = filtered.filter((c) => c.id.startsWith("profile:"));

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  // Focus the input on open, hand focus back to the trigger on close, and own
  // the keyboard: Escape closes, arrows move, Enter runs, Tab stays inside.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    const panel = listRef.current?.closest<HTMLElement>(".palette");
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((i) => (filtered.length ? (i + 1) % filtered.length : 0));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((i) => (filtered.length ? (i - 1 + filtered.length) % filtered.length : 0));
        return;
      }
      if (event.key === "Enter" && filtered.length > 0) {
        event.preventDefault();
        const target = filtered[Math.min(active, filtered.length - 1)];
        target.run();
        close();
        return;
      }
      if (event.key === "Tab" && panel) {
        const focusables = panel.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const current = document.activeElement;
        if (event.shiftKey) {
          if (current === first || !panel.contains(current)) {
            event.preventDefault();
            last.focus();
          }
        } else if (current === last || !panel.contains(current)) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, active, close]);

  return (
    <div className="palette-backdrop" onClick={close}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="palette-search">
          <Search size={15} />
          <input
            ref={inputRef}
            value={query}
            placeholder="Type a command…"
            aria-label="Search commands"
            onChange={(e) => setQuery(e.target.value)}
          />
          <span className="kbd">esc</span>
        </div>
        <div className="palette-list" ref={listRef} role="listbox" aria-label="Commands">
          {visibleActions.map((c, i) => (
            <button
              key={c.id}
              type="button"
              role="option"
              aria-selected={active === i}
              data-index={i}
              className={`palette-item${active === i ? " on" : ""}`}
              onMouseEnter={() => setActive(i)}
              onClick={() => {
                c.run();
                close();
              }}
            >
              <span className="palette-ic">{c.icon}</span>
              <span className="palette-label">{c.label}</span>
              {c.hint && <span className="kbd">{c.hint}</span>}
            </button>
          ))}
          {hasProfiles && visibleProfiles.length > 0 && (
            <>
              <div className="palette-group">Profiles</div>
              {visibleProfiles.map((c, i) => {
                const index = visibleActions.length + i;
                return (
                  <button
                    key={c.id}
                    type="button"
                    role="option"
                    aria-selected={active === index}
                    data-index={index}
                    className={`palette-item${active === index ? " on" : ""}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => {
                      c.run();
                      close();
                    }}
                  >
                    <span className="palette-ic">{c.icon}</span>
                    <span className="palette-label">{c.label}</span>
                    {c.id === `profile:${activeProfile}` && (
                      <span className="palette-tag">active</span>
                    )}
                  </button>
                );
              })}
            </>
          )}
          {filtered.length === 0 && (
            <div className="palette-empty">No matching commands</div>
          )}
        </div>
        <div className="palette-foot mono">
          {commands.length} commands · ↑↓ navigate · ↵ run · esc close
        </div>
      </div>
    </div>
  );
}
