#!/usr/bin/env bash
# Throtl Waybar module: live download/upload + shaping state. Poll via a custom module.
set -u
snap=$(timeout 3 throtl-cli list-processes --json 2>/dev/null) || snap=""
python3 - "$snap" <<'PY'
import json, sys
raw = sys.argv[1]
if not raw:
    print(json.dumps({"text": "⏸", "tooltip": "throtl daemon not reachable",
                      "class": "offline"}))
    raise SystemExit
try:
    s = json.loads(raw)
except ValueError:
    print(json.dumps({"text": "⏸", "tooltip": "throtl daemon not reachable",
                      "class": "offline"}))
    raise SystemExit

def fmt(kbit):
    if kbit is None:
        return "—"
    if kbit >= 1000:
        return f"{kbit/1000:.1f}M"
    return f"{kbit:.0f}K"

g = s.get("global") or {}
on = bool(s.get("enabled"))
dl = fmt(g.get("download"))
ul = fmt(g.get("upload"))
iface = s.get("interface") or "?"
text = f"⏬{dl} ↑{ul}" if on else "⏸"
tooltip = (f"throtl {'on' if on else 'off'} on {iface}\n"
           f"down {dl} · up {ul} · click to toggle")
print(json.dumps({"text": text, "tooltip": tooltip,
                  "class": "on" if on else "off"}))
PY
