"""Persistent per-application bandwidth statistics (item 5).

The GUI shows live bandwidth only in RAM — on close it is all gone. This module
keeps a rolling ring-buffer archive in the daemon so that questions like "how
much did I download per app this week?" can be answered later.

Data model
----------

Per monitoring tick the instantaneous rate (kbit/s) of each app is converted to
**bytes** and added to the current bucket of the respective resolution::

    bytes += rate_kbit_per_s * 1000 / 8 * interval_s

Three fixed resolutions are maintained in parallel (rolling):

===========  =============  =============  =====================
Window       Bucket size    Bucket count   Coverage
===========  =============  =============  =====================
``minute``   60 s           60              1 hour
``hour``     3600 s         48              2 days
``day``      86400 s        30              30 days
===========  =============  =============  =====================

Each bucket is a ``dict`` of app name to ``{"download", "upload"}`` in bytes. A
bucket is addressed by its ABSOLUTE bucket number (``int(now) // bucket_size``);
its ring index is ``id % count``. When a slot is reused for a new absolute id it
is cleared — so old buckets fall out automatically (ring wrap). Additionally,
when jumping more than one ring length all stale slots are discarded.

Persistence
-----------

Stored atomically (``write_text_atomic``) under ``<config_dir>/stats.json`` —
but only every ``save_every`` ticks (default 30) and on ``flush()``/``reset()``,
so it is not written every second. A broken file leads to an empty start (like
``load_config``), never to a crash.

The module deliberately has no third-party dependencies; time comes from
``time.time()`` and is injectable via the ``now`` parameter (tests).
"""

import functools
import json
import os
import shutil
import threading
import time

from . import write_text_atomic

STATS_FILE_NAME = "stats.json"
DEFAULT_INTERVAL = 1.0
DEFAULT_SAVE_EVERY = 30

# window -> (bucket_size in s, bucket count)
RESOLUTIONS = {
    "minute": (60, 60),
    "hour": (3600, 48),
    "day": (86400, 30),
}
VALID_WINDOWS = tuple(RESOLUTIONS)


def _synchronized(method):
    """Serialise a StatsStore access via ``self._lock``.

    The monitor thread writes while RPC threads (GUI/CLI) read. Without the lock
    the buckets' read-modify-write could overlap (lost bytes) and ``json.dumps``
    could run during a mutation. Reentrant, because e.g. ``record`` internally
    calls ``flush``.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class StatsStore:
    """Rolling byte counter per app in three resolutions.

    ``path`` can be set explicitly; alternatively ``config_dir`` + ``stats.json``
    is used. Without ``path`` (and without ``config_dir``) the store works purely
    in memory (handy for tests).
    """

    def __init__(self, config_dir: str | None = None, path: str | None = None,
                 interval: float = DEFAULT_INTERVAL,
                 save_every: int = DEFAULT_SAVE_EVERY):
        self.interval = max(float(interval), 0.0)
        self.save_every = max(int(save_every), 1)
        if path is None and config_dir is not None:
            path = os.path.join(config_dir, STATS_FILE_NAME)
        self.path = path
        self._lock = threading.RLock()
        self._ticks = 0
        self._rings = {
            name: {
                "size": size,
                "count": count,
                "ids": [None] * count,
                "buckets": [dict() for _ in range(count)],
            }
            for name, (size, count) in RESOLUTIONS.items()
        }
        self._load()

    # --- Persistenz -------------------------------------------------------

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._from_json(data)
        except (OSError, ValueError, TypeError, KeyError):
            # Broken/incompatible file: set it aside before starting empty, so
            # the next persist does not silently overwrite the old history.
            self._backup_broken_file()
            self._reset_rings()
        # A restart after a long downtime leaves buckets older than their window
        # in the rings. Expire them now: the first snapshot/budget read happens
        # before the first tick's record(), which would otherwise return them.
        self._expire_all()

    def _backup_broken_file(self) -> str | None:
        """Copy an unreadable stats.json aside (best effort)."""
        if not self.path:
            return None
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = f"{self.path}.invalid-{stamp}"
            shutil.copy2(self.path, target)
            return target
        except OSError:
            return None

    def _expire_all(self, now: float | None = None) -> None:
        timestamp = int(now if now is not None else time.time())
        for ring in self._rings.values():
            self._expire_old(ring, timestamp // ring["size"])

    def _from_json(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("stats.json: root is not a table")
        raw_rings = data.get("rings")
        if not isinstance(raw_rings, dict):
            raise ValueError("stats.json: 'rings' missing")
        # Validate completely first, then adopt — otherwise an error in the
        # middle would leave a half-loaded state behind.
        loaded = {}
        for name, (size, count) in RESOLUTIONS.items():
            raw = raw_rings.get(name)
            if not isinstance(raw, dict):
                raise ValueError(f"stats.json: resolution {name!r} missing")
            ids = raw.get("ids")
            buckets = raw.get("buckets")
            if not isinstance(ids, list) or len(ids) != count:
                raise ValueError(f"stats.json: 'ids' for {name!r} invalid")
            if not isinstance(buckets, list) or len(buckets) != count:
                raise ValueError(f"stats.json: 'buckets' for {name!r} invalid")
            clean_buckets = []
            for bucket in buckets:
                clean_buckets.append(self._clean_bucket(bucket))
            clean_ids = [int(x) if isinstance(x, int) else None for x in ids]
            loaded[name] = {
                "size": size,
                "count": count,
                "ids": clean_ids,
                "buckets": clean_buckets,
            }
        self._rings = loaded
        try:
            self.interval = max(float(data.get("interval")), 0.0)
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _clean_bucket(bucket) -> dict:
        result = {}
        if not isinstance(bucket, dict):
            return result
        for app, values in bucket.items():
            if not isinstance(values, dict):
                continue
            try:
                download = float(values.get("download", 0.0) or 0.0)
                upload = float(values.get("upload", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            result[str(app)] = {"download": download, "upload": upload}
        return result

    def _to_json(self) -> dict:
        return {
            "version": 1,
            "interval": self.interval,
            "rings": {
                name: {
                    "ids": list(ring["ids"]),
                    "buckets": ring["buckets"],
                }
                for name, ring in self._rings.items()
            },
        }

    @_synchronized
    def flush(self) -> None:
        """Write the current state atomically and reset the tick counter."""
        self._ticks = 0
        if not self.path:
            return
        text = json.dumps(self._to_json(), ensure_ascii=False, indent=2)
        # 0600: the statistics describe the account's usage behaviour.
        write_text_atomic(self.path, text + "\n", mode=0o600)

    # --- Aufzeichnen ------------------------------------------------------

    @_synchronized
    def record(self, app_name: str, download_kbit: float = 0.0,
               upload_kbit: float = 0.0, now: float | None = None,
               interval: float | None = None) -> None:
        """Record one monitoring tick for an app.

        ``interval`` is the tick length in seconds (default: ``self.interval``).
        Rates (kbit/s) are converted to bytes and added to the three resolutions.
        """
        if now is None:
            now = time.time()
        step = self.interval if interval is None else max(float(interval), 0.0)
        timestamp = int(now)
        down_bytes = float(download_kbit or 0.0) * 1000.0 / 8.0 * step
        up_bytes = float(upload_kbit or 0.0) * 1000.0 / 8.0 * step
        name = str(app_name)

        for ring in self._rings.values():
            bucket_id = timestamp // ring["size"]
            self._expire_old(ring, bucket_id)
            index = bucket_id % ring["count"]
            if ring["ids"][index] != bucket_id:
                ring["ids"][index] = bucket_id
                ring["buckets"][index] = {}
            bucket = ring["buckets"][index]
            entry = bucket.get(name)
            if entry is None:
                entry = bucket[name] = {"download": 0.0, "upload": 0.0}
            entry["download"] += down_bytes
            entry["upload"] += up_bytes

        self._ticks += 1
        if self.path and self._ticks >= self.save_every:
            self.flush()

    @staticmethod
    def _expire_old(ring: dict, bucket_id: int) -> None:
        """Discard slots that no longer belong in the current window.

        Necessary when the clock jumps (e.g. > ring length) without every
        intermediate bucket having been written. Without this cleanup very old
        buckets would show up in the snapshot.
        """
        lower = bucket_id - ring["count"] + 1
        for index, slot_id in enumerate(ring["ids"]):
            if slot_id is not None and slot_id < lower:
                ring["ids"][index] = None
                ring["buckets"][index] = {}

    # --- Auswertung -------------------------------------------------------

    def _ring(self, window: str) -> dict:
        try:
            return self._rings[window]
        except KeyError:
            raise ValueError(
                f"unknown time window {window!r} "
                f"(allowed: {', '.join(VALID_WINDOWS)})"
            ) from None

    @_synchronized
    def snapshot(self, window: str = "minute") -> list:
        """Aggregated bytes per app in the window, descending by total volume."""
        ring = self._ring(window)
        totals: dict = {}
        for bucket in ring["buckets"]:
            for app, values in bucket.items():
                entry = totals.get(app)
                if entry is None:
                    entry = totals[app] = {"download": 0.0, "upload": 0.0}
                entry["download"] += values.get("download", 0.0)
                entry["upload"] += values.get("upload", 0.0)
        result = [
            {"app": app, "download": entry["download"], "upload": entry["upload"]}
            for app, entry in totals.items()
        ]
        result.sort(key=lambda item: item["download"] + item["upload"], reverse=True)
        return result

    @_synchronized
    def totals(self, window: str = "minute") -> dict:
        download = upload = 0.0
        for item in self.snapshot(window):
            download += item["download"]
            upload += item["upload"]
        return {"download": download, "upload": upload}

    @_synchronized
    def recent_totals(self, window: str, buckets: int,
                      now: float | None = None) -> dict:
        """Bytes per app over the last ``buckets`` time units (rolling).

        For budgets: "last 24 h" = 24 full hour buckets + the current (partial)
        bucket, "last 7 days" = 7 day buckets + the current one. The current
        bucket counts, so a rolling window never undercounts (the boundary would
        otherwise cut off up to one bucket).
        """
        ring = self._ring(window)
        timestamp = int(now if now is not None else time.time())
        now_id = timestamp // ring["size"]
        lower = now_id - max(1, int(buckets))
        totals: dict = {}
        for slot_id, bucket in zip(ring["ids"], ring["buckets"]):
            if slot_id is None or slot_id < lower:
                continue
            for app, values in bucket.items():
                entry = totals.setdefault(app, {"download": 0.0, "upload": 0.0})
                entry["download"] += values.get("download", 0.0)
                entry["upload"] += values.get("upload", 0.0)
        return totals

    @_synchronized
    def series(self, window: str, now: float | None = None) -> list:
        """Bucket time series (oldest first) for the statistics graph."""
        ring = self._ring(window)
        timestamp = int(now if now is not None else time.time())
        count = ring["count"]
        current_index = (timestamp // ring["size"]) % count
        result = []
        for offset in range(count - 1, -1, -1):
            index = (current_index - offset) % count
            slot_id = ring["ids"][index]
            bucket = ring["buckets"][index] if slot_id is not None else {}
            download = sum(v.get("download", 0.0) for v in bucket.values())
            upload = sum(v.get("upload", 0.0) for v in bucket.values())
            result.append({"id": slot_id, "download": download, "upload": upload})
        return result

    @_synchronized
    def reset(self, persist: bool = True) -> None:
        """Clear all buckets. By default the empty state is written."""
        self._reset_rings()
        self._ticks = 0
        if persist and self.path:
            self.flush()

    def _reset_rings(self) -> None:
        self._rings = {
            name: {
                "size": size,
                "count": count,
                "ids": [None] * count,
                "buckets": [dict() for _ in range(count)],
            }
            for name, (size, count) in RESOLUTIONS.items()
        }
