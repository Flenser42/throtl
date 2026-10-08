#!/usr/bin/env bash
# Throtl Waybar module: shaping on/off + interface. Poll this via a custom module.
set -u
status=$(timeout 3 throtl-cli status --json 2>/dev/null) || status=""
if [ -z "$status" ]; then
    printf '{"text": "⏸", "tooltip": "throtl daemon not reachable", "class": "offline"}\n'
    exit 0
fi
python3 - "$status" <<'PY'
import json, sys
s = json.loads(sys.argv[1])
on = bool(s.get("enabled"))
iface = s.get("interface") or "?"
text = f"⏬ {iface}" if on else "⏸"
tooltip = f"throtl shaping {'on' if on else 'off'} on {iface}"
print(json.dumps({"text": text, "tooltip": tooltip,
                  "class": "on" if on else "off"}))
PY
