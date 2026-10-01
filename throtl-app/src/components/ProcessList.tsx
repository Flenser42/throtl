import { useEffect, useRef, useState } from "react";

import { formatBytes, formatRate, splitRate } from "../lib/format";
import type { AppRow } from "../lib/model";
import type { Unit } from "../lib/types";
import {
  ChevronsDown,
  ChevronsUp,
  Clock,
  Copy,
  EllipsisVertical,
  Gauge,
  Minus,
  Plus,
  Refresh,
  Search,
  Trash,
} from "./icons";

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

/** A runnable `throtl set-process …` equivalent for the rule. */
function cliFor(app: AppRow, unit: Unit): string {
  const parts = ["throtl set-process", `--appname "${app.matchValue ?? app.name}"`];
  if (app.downloadLimit != null)
    parts.push(`--download-limit "${formatRate(app.downloadLimit, unit, 2)}"`);
  if (app.uploadLimit != null)
    parts.push(`--upload-limit "${formatRate(app.uploadLimit, unit, 2)}"`);
  if (app.priority && app.priority !== "normal") parts.push(`--priority ${app.priority}`);
  return parts.join(" ");
}

function Rate({ kbit, unit, kind }: { kbit: number; unit: Unit; kind: "down" | "up" }) {
  const { value, unit: label } = splitRate(kbit, unit, 2);
  return (
    <span className={kind === "down" ? "t-down" : "t-up"}>
      <span className="k">{kind === "down" ? "DL" : "UL"}</span> {value} <span className="u">{label}</span>
    </span>
  );
}

interface RowProps {
  app: AppRow;
  unit: Unit;
  onToggleArm: (key: string, armed: boolean) => void;
  onEdit: (app: AppRow) => void;
  onRemove: (app: AppRow) => void;
  onAddRule: (app?: AppRow) => void;
  onReset: () => void;
  onToast: (message: string, kind?: "info" | "error") => void;
  /** ?menu=1 auto-opens the first row's menu (screenshots/dev). */
  autoMenu?: boolean;
}

function ProcessRow({ app, unit, onToggleArm, onEdit, onRemove, onAddRule, onReset, onToast, autoMenu }: RowProps) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const toggleMenu = (target: HTMLElement) => {
    if (open) {
      setOpen(false);
      return;
    }
    const rect = target.getBoundingClientRect();
    const width = 200;
    const height = app.unattributed ? 140 : 180;
    const openUp = rect.bottom + height > window.innerHeight;
    setPos({
      top: openUp ? rect.top - height - 6 : rect.bottom + 6,
      left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
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
          <div className="menu-wrap">
            <button
              type="button"
              className="overflow"
              aria-label={`More actions for ${app.name}`}
              aria-haspopup="menu"
              aria-expanded={open}
              ref={btnRef}
              onClick={(e) => toggleMenu(e.currentTarget)}
            >
              <EllipsisVertical size={16} />
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
                      <Plus size={14} /> Create rule…
                    </button>
                  ) : (
                    <button
                      role="menuitem"
                      onClick={() => {
                        setOpen(false);
                        onEdit(app);
                      }}
                    >
                      <Gauge size={14} /> Edit rule
                    </button>
                  )}
                  <button
                    role="menuitem"
                    onClick={() => {
                      setOpen(false);
                      onReset();
                    }}
                  >
                    <Refresh size={14} /> Reset counters
                  </button>
                  <button
                    role="menuitem"
                    onClick={() => {
                      setOpen(false);
                      copyCli();
                    }}
                  >
                    <Copy size={14} /> Copy CLI command
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
                        <Trash size={14} /> Remove rule
                      </button>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="r-bottom">
        {app.unattributed ? (
          <button type="button" className="btn" style={{ height: 24, fontSize: 11.5 }} onClick={() => onAddRule(app)}>
            <Plus size={13} /> Create rule…
          </button>
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

  return (
    <div className="rows">
      {sorted.length === 0 ? (
        <div className="empty">
          <span className="empty-icon">
            <Search size={22} />
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
        sorted.map((app) => (
          <ProcessRow
            key={app.key}
            app={app}
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
        Showing {groupsVisible} of {groupsTotal} groups · sorted by {sortKey}
      </div>
    </div>
  );
}
