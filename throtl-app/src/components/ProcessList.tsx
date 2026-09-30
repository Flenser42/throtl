import { formatBytes, splitRate } from "../lib/format";
import type { AppRow } from "../lib/model";
import type { Unit } from "../lib/types";
import { ChevronsDown, ChevronsUp, Clock, EllipsisVertical, Gauge, Minus } from "./icons";

const PRIORITY_LABEL: Record<string, string> = {
  kritisch: "Critical",
  hoch: "High",
  normal: "Normal",
  niedrig: "Low",
};

function PriorityIcon({ priority }: { priority: string }) {
  if (priority === "kritisch" || priority === "hoch") return <ChevronsUp size={13} />;
  if (priority === "niedrig") return <ChevronsDown size={13} />;
  return <Minus size={13} />;
}

function limitChipText(app: AppRow, unit: Unit): string {
  const dl = app.downloadLimit;
  const ul = app.uploadLimit;
  if (dl == null && ul == null) return "unlimited";
  const dlText = dl != null ? `${splitRate(dl, unit, 2).value} ${splitRate(dl, unit, 2).unit} ↓` : "—";
  const ulText = ul != null ? `${splitRate(ul, unit, 2).value} ↑` : "—";
  return `${dlText} · ${ulText}`;
}

function Rate({ kbit, unit, kind }: { kbit: number; unit: Unit; kind: "down" | "up" }) {
  const { value, unit: label } = splitRate(kbit, unit, 2);
  return (
    <span className={kind === "down" ? "t-down" : "t-up"}>
      <span className="k">{kind === "down" ? "DL" : "UL"}</span> {value} <span className="u">{label}</span>
    </span>
  );
}

function ProcessRow({
  app,
  unit,
  onToggleArm,
  onEdit,
}: {
  app: AppRow;
  unit: Unit;
  onToggleArm: (key: string, armed: boolean) => void;
  onEdit: (app: AppRow) => void;
}) {
  return (
    <div className={`row${app.unattributed ? " unattr" : ""}`}>
      <div className="r-top">
        <span className="appicon" style={{ background: app.gradient }}>
          {app.initial}
        </span>
        <div className="r-id">
          <div className="r-name">{app.name}</div>
          <div className="r-meta">{app.meta}</div>
        </div>

        <div className="r-actions">
          <div className="rates">
            <Rate kbit={app.downKbit} unit={unit} kind="down" />
            <Rate kbit={app.upKbit} unit={unit} kind="up" />
          </div>
          {!app.unattributed && (
            <button
              type="button"
              className={`switch${app.armed ? " on" : ""}`}
              role="switch"
              aria-checked={app.armed}
              aria-label={`Limit ${app.name}`}
              onClick={() => onToggleArm(app.key, !app.armed)}
            />
          )}
          <button type="button" className="overflow" aria-label={`More actions for ${app.name}`}>
            <EllipsisVertical size={16} />
          </button>
        </div>
      </div>

      <div className="r-bottom">
        {app.unattributed ? (
          <>
            <span className="meta-item">no rule</span>
            <button type="button" className="btn" style={{ height: 24, fontSize: 11.5 }}>
              Create rule…
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={`chip${app.downloadLimit != null || app.uploadLimit != null ? " acc" : ""}`}
              onClick={() => onEdit(app)}
            >
              <Gauge />
              {limitChipText(app, unit)}
            </button>
            <span className="meta-item">
              <PriorityIcon priority={app.priority} />
              {PRIORITY_LABEL[app.priority] ?? "Normal"}
            </span>
            {app.windowLabel && (
              <span className={`chip${app.windowActive ? " acc" : ""}`} style={app.windowActive ? undefined : { opacity: 0.6 }}>
                <Clock />
                {app.windowActive ? `${app.windowLabel} · now` : app.windowState ?? app.windowLabel}
              </span>
            )}
            {app.budget && (
              <span className="mini">
                <span className="bar">
                  <i
                    style={{
                      width: `${Math.round(Math.min(1, app.budget.ratio) * 100)}%`,
                      background:
                        app.budget.ratio >= 0.8
                          ? "linear-gradient(90deg,var(--warn),color-mix(in oklab,var(--warn) 60%,white))"
                          : undefined,
                    }}
                  />
                </span>
                <span>
                  {formatBytes(app.budget.used, 1).replace(/ (GB|MB|TB)$/, "")}/
                  {formatBytes(app.budget.limit, 0)}
                </span>
              </span>
            )}
          </>
        )}
      </div>
    </div>
  );
}

interface Props {
  apps: AppRow[];
  unit: Unit;
  sortKey: "download" | "upload" | "name";
  onToggleArm: (key: string, armed: boolean) => void;
  onEdit: (app: AppRow) => void;
  groupsVisible: number;
  groupsTotal: number;
}

export function ProcessList({
  apps,
  unit,
  sortKey,
  onToggleArm,
  onEdit,
  groupsVisible,
  groupsTotal,
}: Props) {
  const sorted = [...apps].sort((a, b) => {
    if (sortKey === "name") return a.name.localeCompare(b.name);
    if (sortKey === "upload") return b.upKbit - a.upKbit;
    return b.downKbit - a.downKbit;
  });

  return (
    <div className="rows">
      {sorted.map((app) => (
        <ProcessRow
          key={app.key}
          app={app}
          unit={unit}
          onToggleArm={onToggleArm}
          onEdit={onEdit}
        />
      ))}
      <div className="hint">
        Showing {groupsVisible} of {groupsTotal} groups · sorted by {sortKey}
      </div>
    </div>
  );
}
