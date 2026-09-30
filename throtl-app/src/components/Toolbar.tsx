import type { RefObject } from "react";

import { Search } from "./icons";

interface Props {
  query: string;
  onQuery: (value: string) => void;
  sortKey: "download" | "upload" | "name";
  onSort: (key: "download" | "upload" | "name") => void;
  onAddRule: () => void;
  inputRef?: RefObject<HTMLInputElement | null>;
}

const SORTS: { id: "download" | "upload" | "name"; label: string }[] = [
  { id: "download", label: "Download" },
  { id: "upload", label: "Upload" },
  { id: "name", label: "Name" },
];

export function Toolbar({ query, onQuery, sortKey, onSort, onAddRule, inputRef }: Props) {
  return (
    <div className="toolbar">
      <label className="search">
        <Search size={15} />
        <input
          ref={inputRef}
          placeholder="Filter applications…"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label="Filter applications"
        />
        <span className="kbd">⌘K</span>
      </label>
      <div className="seg">
        {SORTS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={sortKey === s.id ? "on" : ""}
            onClick={() => onSort(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
      <button type="button" className="btn" style={{ marginLeft: "auto" }} onClick={onAddRule}>
        + Add rule
      </button>
    </div>
  );
}
