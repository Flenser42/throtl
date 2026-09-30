import { ChartLine, Pause, Play, Settings } from "./icons";

interface Props {
  enabled: boolean;
  profile: string;
  onToggle: (enabled: boolean) => void;
  onSettings: () => void;
  onStats: () => void;
}

export function Header({ enabled, profile, onToggle, onSettings, onStats }: Props) {
  return (
    <header className="app-header">
      <button
        type="button"
        className={`status-pill${enabled ? "" : " paused"}`}
        onClick={() => onToggle(!enabled)}
        aria-label={`Shaping ${enabled ? "on" : "off"}. Profile ${profile}. Click to toggle.`}
      >
        <span className={`dot${enabled ? "" : " off"}`} />
        {enabled ? "Limiting" : "Paused"}
        <span className="pill-sep" />
        {profile}
      </button>

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
          title="Settings"
          aria-label="Settings"
          onClick={onSettings}
        >
          <Settings />
        </button>
        <div className="win-ctrl" aria-hidden="true">
          <i />
          <i />
          <i />
        </div>
      </div>
    </header>
  );
}
