import type { ReactNode } from "react";

import { autoRate } from "../lib/format";
import type { HistoryPoint } from "../lib/model";
import { ChartLine, ChevronDown, CircleHelp, Pause, Play, Settings, Wallet } from "./icons";
import { Sparkline } from "./Sparkline";

interface Props {
  enabled: boolean;
  profile: string;
  /** Live 1 Hz samples for the header sparkline (recent download activity). */
  history: HistoryPoint[];
  onToggle: (enabled: boolean) => void;
  onSettings: () => void;
  onStats: () => void;
  onBudgets: () => void;
  onTour: () => void;
  profileOpen: boolean;
  onProfileToggle: () => void;
  /** Adds a soft shadow once the page scrolls under the sticky header. */
  scrolled: boolean;
  /** The profile popover, rendered under the pill when open. */
  profiles: ReactNode;
}

export function Header({
  enabled,
  profile,
  history,
  onToggle,
  onSettings,
  onStats,
  onBudgets,
  onTour,
  profileOpen,
  onProfileToggle,
  scrolled,
  profiles,
}: Props) {
  const recent = history.slice(-40);
  const avg =
    recent.length > 0 ? recent.reduce((sum, p) => sum + p.down, 0) / recent.length : 0;
  const avgRate = autoRate(avg, 1);
  const sparkLabel =
    recent.length >= 2
      ? `Recent download activity while ${enabled ? "limiting" : "paused"}: ` +
        `average ${avgRate.value} ${avgRate.unit} over the last ` +
        `${recent.length} seconds`
      : "No recent download activity";

  return (
    <header className={`app-header${scrolled ? " scrolled" : ""}`}>
      <div className="pill-anchor">
        <div className={`pill-group${enabled ? "" : " paused"}`} data-tour="status">
          <button
            type="button"
            className="status-pill"
            onClick={() => onToggle(!enabled)}
            aria-label={`Shaping ${enabled ? "on" : "off"}. Profile ${profile}. Click to toggle.`}
          >
            <span className={`dot${enabled ? "" : " off"}`} />
            {enabled ? "Limiting" : "Paused"}
            <span className="pill-sep" />
            {profile}
          </button>
          <button
            type="button"
            className="pill-caret"
            aria-label="Switch profile"
            aria-haspopup="menu"
            aria-expanded={profileOpen}
            onClick={onProfileToggle}
          >
            <ChevronDown size={14} />
          </button>
        </div>
        {profileOpen && profiles}
      </div>

      <div className="brand">
        <span className="logo" />
        Throtl
      </div>

      <div className="hright" data-tour="actions">
        <div className="spark-wrap">
          <span className="sr-only">{sparkLabel}</span>
          <Sparkline history={history} />
        </div>
        <button
          type="button"
          className="icon-btn"
          title={enabled ? "Pause shaping" : "Resume shaping"}
          aria-label={enabled ? "Pause shaping" : "Resume shaping"}
          onClick={() => onToggle(!enabled)}
        >
          {enabled ? <Pause /> : <Play />}
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Statistics"
          aria-label="Statistics"
          onClick={onStats}
        >
          <ChartLine />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Budgets"
          aria-label="Budgets"
          onClick={onBudgets}
        >
          <Wallet />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Settings"
          aria-label="Settings"
          onClick={onSettings}
        >
          <Settings />
        </button>
        <button
          type="button"
          className="icon-btn"
          title="Take a tour"
          aria-label="Take a tour"
          onClick={onTour}
        >
          <CircleHelp />
        </button>
      </div>
    </header>
  );
}
