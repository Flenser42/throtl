#!/usr/bin/env python3
"""Add a widget id to an Omarchy shell.json bar section (idempotent).

Usage: register-widget.py <shell.json> <widget-id>

Inserts {"id": "<widget-id>"} into bar.layout.right if it is not already
present anywhere in the layout, and preserves the rest of the file. Used by
setup/install.sh to activate the Throtl bar widget after copying its plugin.
"""

import json
import os
import sys


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    path, widget_id = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as handle:
        cfg = json.load(handle)

    bar = cfg.setdefault("bar", {})
    layout = bar.setdefault("layout", {})
    sections = layout.setdefault("right", [])

    for entry in sections:
        if isinstance(entry, dict) and entry.get("id") == widget_id:
            return  # already registered

    sections.append({"id": widget_id})
    layout["right"] = sections

    # Atomic write with a backup, so a broken edit never leaves no shell.json.
    backup = f"{path}.bak.throtl.{os.getpid()}"
    with open(path, encoding="utf-8") as handle:
        original = handle.read()
    with open(backup, "w", encoding="utf-8") as handle:
        handle.write(original)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
