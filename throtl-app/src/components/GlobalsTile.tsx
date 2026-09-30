import { splitRate, unitLabel } from "../lib/format";
import type { Unit } from "../lib/types";

interface Props {
  unit: Unit;
  downLimit: number | null;
  upLimit: number | null;
  priority: string;
  profile: string;
}

const PRIORITY: Record<string, string> = {
  kritisch: "Critical",
  hoch: "High",
  normal: "Normal",
  niedrig: "Low",
};

export function GlobalsTile({ unit, downLimit, upLimit, priority, profile }: Props) {
  return (
    <div className="card tile">
      <div className="tile-label">Global caps</div>
      <div className="tile-value mono">
        {downLimit == null ? (
          <span className="unit" style={{ fontSize: 20, marginLeft: 0 }}>
            unlimited
          </span>
        ) : (
          <>
            {splitRate(downLimit, unit, 0).value}
            <span className="unit">{unitLabel(unit)} ↓</span>
          </>
        )}
      </div>
      <div className="caps">
        <div className="cap-row">
          <span>Upload cap</span>
          <span className="cap-val">
            {upLimit == null ? "unlimited" : `${splitRate(upLimit, unit, 1).value} ${unitLabel(unit)} ↑`}
          </span>
        </div>
        <div className="cap-row">
          <span>Priority</span>
          <span className="cap-val">{PRIORITY[priority] ?? "Normal"}</span>
        </div>
        <div className="cap-row">
          <span>Profile</span>
          <span className="cap-val">{profile}</span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 12 }}>
        <button type="button" className="btn">
          Edit caps
        </button>
        <button type="button" className="btn btn-ghost">
          Profiles
        </button>
      </div>
    </div>
  );
}
