import { useEffect, useRef, useState } from "react";

import { formatRate, splitRate } from "../lib/format";
import type { AppRow } from "../lib/model";
import type { Unit } from "../lib/types";
import {
  Clock,
  Copy,
  EllipsisVertical,
  Plus,
  Refresh,
  Search,
  Trash,
} from "./icons";

/** Dense abbreviations: a table header carries the meaning, the cell stays short. */
const PRIORITY_SHORT: Record<string, string> = {
  kritisch: "CRIT",
  hoch: "HIGH",
  normal: "NORM",
  niedrig: "LOW",
};

/** A runnable `throtl-cli set-process …` equivalent for the rule. */
function cliFor(app: AppRow, unit: Unit): string {
  const parts = ["throtl-cli set-process", `--appname "${app.matchValue ?? app.name}"`];
  if (app.downloadLimit != null)
    parts.push(`--download-limit "${formatRate(app.downloadLimit, unit, 2)}"`);
  if (app.uploadLimit != null)
    parts.push(`--upload-limit "${formatRate(app.uploadLimit, unit, 2)}"`);
  if (app.priority && app.priority !== "normal") parts.push(`--priority ${app.priority}`);
  return parts.join(" ");
}

interface RowProps {
  app: AppRow;
  unit: Unit;
  index: number;
  onToggleArm: (key: string, armed: boolean) => void;
  onEdit: (app: AppRow) => void;
  onRemove: (app: AppRow) => void;
  onAddRule: (app?: AppRow) => void;
  onReset: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
  /** ?menu=1 auto-opens the first row's menu (screenshots/dev). */
  autoMenu?: boolean;
}

function ProcessRow({
  app,
  unit,
  index,
  onToggleArm,
  onEdit,
  onRemove,
  onAddRule,
  onReset,
  onToast,
  autoMenu,
}: RowProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const toggleMenu = (target: HTMLElement) => {
    if (open) {
      setOpen(false);
      return;
    }
    const rect = target.getBoundingClientRect();
    const width = 190;
    const height = app.unattributed ? 130 : 160;
    const openUp = rect.bottom + height > window.innerHeight;
    setPos({
      top: openUp ? rect.top - height - 4 : rect.bottom + 4,
      left: Math.max(4, Math.min(rect.right - width, window.innerWidth - width - 4)),
    });
    setOpen(true);
  };

  useEffect(() => {
    if (autoMenu && btnRef.current) toggleMenu(btnRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const copyCli = () => {
    const text = cliFor(app, unit);
    navigator.clipboard?.writeText(text).then(
      () => onToast("CLI command copied"),
      () => onToast("Could not copy", "error"),
    );
  };

  const dl = splitRate(app.downKbit, unit, 2);
  const ul = splitRate(app.upKbit, unit, 2);
  const limit =
    app.downloadLimit == null && app.uploadLimit == null
      ? null
      : {
          down: app.downloadLimit != null ? splitRate(app.downloadLimit, unit, 2) : null,
          up: app.uploadLimit != null ? splitRate(app.uploadLimit, unit, 2) : null,
        };

  return (
    <div className={`row${app.unattributed ? " unattr" : ""}`}>
      <span className="r-index mono">{String(index + 1).padStart(2, "0")}</span>
      <span className="appicon">{app.initial}</span>

      <span className="r-id">
        <span className="r-name">{app.name}</span>
        <span className="r-meta">{app.meta}</span>
      </span>

      <span className="rate-cell mono t-down" title="download">
        {dl.value}
        <span className="rate-unit">{dl.unit}</span>
      </span>
      <span className="rate-cell mono t-up" title="upload">
        {ul.value}
        <span className="rate-unit">{ul.unit}</span>
      </span>

      <span className="r-limit">
        {limit ? (
          <button type="button" className="chip acc" onClick={() => onEdit(app)}>
            {limit.down ? `${limit.down.value} ↓` : "—"}
            {"  "}
            {limit.up ? `${limit.up.value} ↑` : "—"}
          </button>
        ) : (
          <button type="button" className="chip" onClick={() => onEdit(app)}>
            no limit
          </button>
        )}
      </span>

      <span className="r-pri">
        {app.unattributed ? <span className="meta-item">—</span> : (
          <span className="meta-item">{PRIORITY_SHORT[app.priority] ?? "NORM"}</span>
        )}
      </span>

      <span className="r-budget">
        {app.budget ? (
          <span className="mini" title={`${app.budget.used} / ${app.budget.limit} bytes`}>
            <span className="bar">
              <i
                style={{
                  width: `${Math.round(Math.min(1, app.budget.ratio) * 100)}%`,
                  background: app.budget.ratio >= 0.8 ? "var(--warn)" : undefined,
                }}
              />
            </span>
            <span className={app.budget.ratio >= 0.8 ? "t-warn" : ""}>
              {Math.round(app.budget.ratio * 100)}%
            </span>
          </span>
        ) : app.windowLabel ? (
          <span className="meta-item" title={app.windowState ?? app.windowLabel}>
            <Clock size={11} />
            {app.windowActive ? "now" : "off"}
          </span>
        ) : (
          <span className="meta-item">—</span>
        )}
      </span>

      {app.unattributed ? (
        <span className="r-arm">
          <button
            type="button"
            className="overflow"
            aria-label={`Create a rule for ${app.name}`}
            title="Create rule"
            onClick={() => onAddRule(app)}
          >
            <Plus size={12} />
          </button>
        </span>
      ) : (
        <span className="r-arm">
          <button
            type="button"
            className={`switch${app.armed ? " on" : ""}`}
            role="switch"
            aria-checked={app.armed}
            aria-label={`Limit ${app.name}`}
            title={app.armed ? "Limit applied" : "No limit applied"}
            onClick={() => onToggleArm(app.key, !app.armed)}
          />
        </span>
      )}

      <span className="menu-wrap r-menu">
        <button
          type="button"
          className="overflow"
          aria-label={`More actions for ${app.name}`}
          aria-haspopup="menu"
          aria-expanded={open}
          ref={btnRef}
          onClick={(e) => toggleMenu(e.currentTarget)}
        >
          <EllipsisVertical size={13} />
        </button>
        {open && pos && (
          <>
            <div className="menu-backdrop" onClick={() => setOpen(false)} />
            <div
              className="menu"
              role="menu"
              style={{ position: "fixed", top: pos.top, left: pos.left }}
            >
              {app.unattributed ? (
                <button
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    onAddRule(app);
                  }}
                >
                  <Plus size={12} /> Create rule…
                </button>
              ) : (
                <button
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    onEdit(app);
                  }}
                >
                  Edit rule
                </button>
              )}
              <button
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onReset();
                }}
              >
                <Refresh size={12} /> Reset counters
              </button>
              <button
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  copyCli();
                }}
              >
                <Copy size={12} /> Copy CLI command
              </button>
              {!app.unattributed && (
                <>
                  <div className="menu-sep" />
                  <button
                    role="menuitem"
                    className="danger"
                    onClick={() => {
                      setOpen(false);
                      onRemove(app);
                    }}
                  >
                    <Trash size={12} /> Remove rule
                  </button>
                </>
              )}
            </div>
          </>
        )}
      </span>
    </div>
  );
}

interface Props {
  apps: AppRow[];
  unit: Unit;
  sortKey: "download" | "upload" | "name";
  onToggleArm: (key: string, armed: boolean) => void;
  onEdit: (app: AppRow) => void;
  onRemove: (app: AppRow) => void;
  onAddRule: (app?: AppRow) => void;
  onReset: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
  /** Current filter text (drives the empty state). */
  query: string;
  onClearQuery: () => void;
  groupsVisible: number;
  groupsTotal: number;
}

export function ProcessList({
  apps,
  unit,
  sortKey,
  onToggleArm,
  onEdit,
  onRemove,
  onAddRule,
  onReset,
  onToast,
  query,
  onClearQuery,
  groupsVisible,
  groupsTotal,
}: Props) {
  const sorted = [...apps].sort((a, b) => {
    if (sortKey === "name") return a.name.localeCompare(b.name);
    if (sortKey === "upload") return b.upKbit - a.upKbit;
    return b.downKbit - a.downKbit;
  });

  const menuParam = new URLSearchParams(location.search).get("menu") === "1";
  const firstRuleKey = sorted.find((a) => !a.unattributed)?.key;
  const unitLabel = splitRate(0, unit, 0).unit;

  return (
    <div className="rows">
      <div className="table-head">
        <span className="r-index">#</span>
        <span />
        <span className="r-id">Application</span>
        <span className="rate-cell">DL {unitLabel}</span>
        <span className="rate-cell">UL {unitLabel}</span>
        <span className="r-limit">Limit</span>
        <span className="r-pri">Pri</span>
        <span className="r-budget">Budget</span>
        <span className="r-arm">Arm</span>
        <span className="r-menu" />
      </div>

      {sorted.length === 0 ? (
        <div className="empty">
          <span className="empty-icon">
            <Search size={16} />
          </span>
          <div className="empty-title">
            {query ? "No matching applications" : "Nothing to show yet"}
          </div>
          <div className="empty-text">
            {query
              ? `Nothing matches “${query}”. Try a different name, or clear the filter.`
              : "Throtl has not seen any traffic yet. Start something that uses the network."}
          </div>
          {query && (
            <button type="button" className="btn" onClick={onClearQuery}>
              Clear filter
            </button>
          )}
        </div>
      ) : (
        sorted.map((app, i) => (
          <ProcessRow
            key={app.key}
            app={app}
            index={i}
            unit={unit}
            onToggleArm={onToggleArm}
            onEdit={onEdit}
            onRemove={onRemove}
            onAddRule={onAddRule}
            onReset={onReset}
            onToast={onToast}
            autoMenu={menuParam && app.key === firstRuleKey}
          />
        ))
      )}

      <div className="hint">
        {groupsVisible} of {groupsTotal} groups · sorted by {sortKey}
      </div>
    </div>
  );
}
