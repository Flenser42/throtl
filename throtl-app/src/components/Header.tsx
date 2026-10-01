import type { ReactNode } from "react";

import { ChartLine, ChevronDown, Pause, Play, Settings, Wallet } from "./icons";

interface Props {
  enabled: boolean;
  profile: string;
  onToggle: (enabled: boolean) => void;
  onSettings: () => void;
  onStats: () => void;
  onBudgets: () => void;
  profileOpen: boolean;
  onProfileToggle: () => void;
  /** The profile popover, rendered under the pill when open. */
  profiles: ReactNode;
}

export function Header({
  enabled,
  profile,
  onToggle,
  onSettings,
  onStats,
  onBudgets,
  profileOpen,
  onProfileToggle,
  profiles,
}: Props) {
  return (
    <header className="app-header">
      <div className="pill-anchor">
        <div className={`pill-group${enabled ? "" : " paused"}`}>
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

      <div className="hright">
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
      </div>
    </header>
  );
}
