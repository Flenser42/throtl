import { Sheet } from "./Sheet";
import { unitLabel } from "../lib/format";
import { Download, Upload } from "./icons";
import type { ConnectionState, Unit } from "../lib/types";

export type Theme = "system" | "light" | "dark";
export type Density = "comfortable" | "compact";

interface Props {
  theme: Theme;
  onTheme: (theme: Theme) => void;
  density: Density;
  onDensity: (density: Density) => void;
  unit: Unit;
  onUnit: (unit: Unit) => void;
  socket: string;
  state: ConnectionState;
  version: string;
  onExport: () => void;
  onImport: (file: File) => void;
  onClose: () => void;
}

const THEMES: { id: Theme; label: string }[] = [
  { id: "system", label: "System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
];

const DENSITIES: { id: Density; label: string }[] = [
  { id: "comfortable", label: "Comfortable" },
  { id: "compact", label: "Compact" },
];

const UNITS: Unit[] = ["mBs", "kBs", "mbps", "kbps"];

export function SettingsSheet({
  theme,
  onTheme,
  density,
  onDensity,
  unit,
  onUnit,
  socket,
  state,
  version,
  onExport,
  onImport,
  onClose,
}: Props) {
  return (
    <Sheet title="Settings" subtitle="Appearance, daemon and about" onClose={onClose}>
      <section className="sheet-group">
        <h3 className="sheet-group-title">Appearance</h3>
        <div className="sheet-row">
          <span className="sheet-key">Theme</span>
          <div className="seg">
            {THEMES.map((t) => (
              <button
                key={t.id}
                type="button"
                className={theme === t.id ? "on" : ""}
                onClick={() => onTheme(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Density</span>
          <div className="seg">
            {DENSITIES.map((d) => (
              <button
                key={d.id}
                type="button"
                className={density === d.id ? "on" : ""}
                onClick={() => onDensity(d.id)}
              >
                {d.label}
              </button>
            ))}
          </div>
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Display unit</span>
          <div className="seg">
            {UNITS.map((u) => (
              <button
                key={u}
                type="button"
                className={unit === u ? "on" : ""}
                onClick={() => onUnit(u)}
              >
                {unitLabel(u)}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Configuration</h3>
        <div className="sheet-actions">
          <button type="button" className="btn" onClick={onExport}>
            <Download size={14} /> Export
          </button>
          <label className="btn file-label">
            <Upload size={14} /> Import
            <input
              type="file"
              accept="application/json,.json"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) onImport(file);
                e.target.value = "";
              }}
            />
          </label>
        </div>
        <p className="sheet-note">Export writes the whole config as JSON; import replaces it.</p>
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">Daemon</h3>
        <div className="sheet-row">
          <span className="sheet-key">Socket</span>
          <code className="sheet-value">{socket}</code>
        </div>
        <div className="sheet-row">
          <span className="sheet-key">Status</span>
          <span className={`pill-state ${state}`}>{state}</span>
        </div>
      </section>

      <section className="sheet-group">
        <h3 className="sheet-group-title">About</h3>
        <div className="sheet-row">
          <span className="sheet-key">Throtl</span>
          <span className="sheet-value">{version}</span>
        </div>
        <p className="sheet-note">
          Shaping is done by TrafficToll and per-process measurement by nethogs;
          the daemon runs as a root systemd service. Local Unix socket only.
        </p>
      </section>
    </Sheet>
  );
}
