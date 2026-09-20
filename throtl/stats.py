"""Persistente Bandbreiten-Statistik pro Anwendung (Punkt 5).

Die GUI zeigt Live-Bandbreite nur im RAM — beim Schliessen ist alles weg.
Dieses Modul haelt im Daemon ein rollierendes Ringpuffer-Archiv vor, damit
spaeter Fragen wie "Wie viel habe ich diese Woche pro App geladen?" beantwortet
werden koennen.

Datenmodell
-----------

Pro Monitoring-Tick wird fuer jede App die Momentanrate (kbit/s) in **Bytes**
umgerechnet und auf den aktuellen Bucket der jeweiligen Aufloesung addiert::

    bytes += rate_kbit_per_s * 1000 / 8 * interval_s

Drei feste Aufloesungen werden parallel gepflegt (rollierend):

===========  =============  =============  =====================
Window       Bucket-Groesse  Anzahl Buckets  Abdeckung
===========  =============  =============  =====================
``minute``   60 s           60              1 Stunde
``hour``     3600 s         48              2 Tage
``day``      86400 s        30              30 Tage
===========  =============  =============  =====================

Jeder Bucket ist ein ``dict`` von App-Name auf ``{"download", "upload"}`` in
Bytes. Ein Bucket wird ueber seine ABSOLUTE Bucket-Nummer (``int(now) //
bucket_size``) adressiert; sein Ring-Index ist ``id % count``. Wird ein Slot
fuer eine neue absolute ID verwendet, wird er geleert — damit fallen alte
Buckets automatisch heraus (Ringschluss). Zusaetzlich werden beim Sprung ueber
mehr als eine Ringlaenge alle veralteten Slots verworfen.

Persistenz
----------

Gespeichert wird atomar (``write_text_atomic``) unter
``<config_dir>/stats.json`` — aber nur alle ``save_every`` Ticks (Default 30)
und bei ``flush()``/``reset()``, damit nicht jede Sekunde geschrieben wird.
Eine kaputte Datei fuehrt zu einem leeren Start (analog zu ``load_config``),
niemals zu einem Crash.

Das Modul kommt bewusst ohne Third-Party-Dependencies aus; die Zeit kommt aus
``time.time()`` und ist ueber den ``now``-Parameter injizierbar (Tests).
"""

import json
import os
import time

from . import write_text_atomic

STATS_FILE_NAME = "stats.json"
DEFAULT_INTERVAL = 1.0
DEFAULT_SAVE_EVERY = 30

# window -> (bucket_size in s, anzahl buckets)
RESOLUTIONS = {
    "minute": (60, 60),
    "hour": (3600, 48),
    "day": (86400, 30),
}
VALID_WINDOWS = tuple(RESOLUTIONS)


class StatsStore:
    """Rollierender Byte-Zaehler pro App in drei Aufloesungen.

    ``path`` kann explizit gesetzt werden; alternativ wird ``config_dir`` +
    ``stats.json`` verwendet. Ohne ``path`` (und ohne ``config_dir``) arbeitet
    der Store rein im Speicher (praktisch fuer Tests).
    """

    def __init__(self, config_dir: str | None = None, path: str | None = None,
                 interval: float = DEFAULT_INTERVAL,
                 save_every: int = DEFAULT_SAVE_EVERY):
        self.interval = max(float(interval), 0.0)
        self.save_every = max(int(save_every), 1)
        if path is None and config_dir is not None:
            path = os.path.join(config_dir, STATS_FILE_NAME)
        self.path = path
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
            # Kaputte/inkompatible Datei: leer starten statt crashen.
            self._reset_rings()

    def _from_json(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("stats.json: Wurzel ist keine Tabelle")
        raw_rings = data.get("rings")
        if not isinstance(raw_rings, dict):
            raise ValueError("stats.json: 'rings' fehlt")
        # Erst vollstaendig validieren, dann uebernehmen — sonst bliebe bei
        # einem Fehler in der Mitte ein halb geladener Zustand zurueck.
        loaded = {}
        for name, (size, count) in RESOLUTIONS.items():
            raw = raw_rings.get(name)
            if not isinstance(raw, dict):
                raise ValueError(f"stats.json: Aufloesung {name!r} fehlt")
            ids = raw.get("ids")
            buckets = raw.get("buckets")
            if not isinstance(ids, list) or len(ids) != count:
                raise ValueError(f"stats.json: 'ids' fuer {name!r} ungueltig")
            if not isinstance(buckets, list) or len(buckets) != count:
                raise ValueError(f"stats.json: 'buckets' fuer {name!r} ungueltig")
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

    def flush(self) -> None:
        """Aktuellen Stand atomar schreiben und Tick-Zaehler zuruecksetzen."""
        self._ticks = 0
        if not self.path:
            return
        text = json.dumps(self._to_json(), ensure_ascii=False, indent=2)
        # 0600: die Statistik beschreibt das Nutzungsverhalten des Kontos.
        write_text_atomic(self.path, text + "\n", mode=0o600)

    # --- Aufzeichnen ------------------------------------------------------

    def record(self, app_name: str, download_kbit: float = 0.0,
               upload_kbit: float = 0.0, now: float | None = None,
               interval: float | None = None) -> None:
        """Einen Monitoring-Tick fuer eine App verbuchen.

        ``interval`` ist die Laenge des Ticks in Sekunden (Default:
        ``self.interval``). Raten (kbit/s) werden in Bytes umgerechnet und auf
        die drei Aufloesungen addiert.
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
        """Slots verwerfen, die nicht mehr ins aktuelle Fenster gehoeren.

        Notwendig, wenn die Uhr weiterspringt (z. B. > Ringlaenge), ohne dass
        jeder Zwischenbucket beschrieben wurde. Ohne diese Bereinigung wuerden
        uralte Buckets im Snapshot auftauchen.
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
                f"unbekanntes Zeitfenster {window!r} "
                f"(erlaubt: {', '.join(VALID_WINDOWS)})"
            ) from None

    def snapshot(self, window: str = "minute") -> list:
        """Aggregierte Bytes pro App im Fenster, absteigend nach Gesamtvolumen."""
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

    def totals(self, window: str = "minute") -> dict:
        download = upload = 0.0
        for item in self.snapshot(window):
            download += item["download"]
            upload += item["upload"]
        return {"download": download, "upload": upload}

    def reset(self, persist: bool = True) -> None:
        """Alle Buckets leeren. Standardmaessig wird der leere Stand geschrieben."""
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
