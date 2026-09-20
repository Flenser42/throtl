"""Tests fuer den persistenten Statistik-Speicher (Aufgabe A)."""

import json
import os
import tempfile
import unittest

from throtl.stats import StatsStore


class ByteConversionTest(unittest.TestCase):
    def test_kbit_to_bytes(self):
        store = StatsStore()  # rein im Speicher
        # 8 kbit/s * 1000 / 8 * 1 s = 1000 Bytes
        store.record("Foo", download_kbit=8, upload_kbit=16, now=0, interval=1)
        snap = {item["app"]: item for item in store.snapshot("minute")}
        self.assertEqual(snap["Foo"]["download"], 1000.0)
        self.assertEqual(snap["Foo"]["upload"], 2000.0)

    def test_interval_scales(self):
        store = StatsStore()
        store.record("Foo", download_kbit=8, now=0, interval=2)
        self.assertEqual(store.snapshot("minute")[0]["download"], 2000.0)


class ThreeResolutionsTest(unittest.TestCase):
    def test_record_writes_all_windows(self):
        store = StatsStore()
        store.record("Foo", download_kbit=8000, now=0, interval=1)
        for window in ("minute", "hour", "day"):
            snap = store.snapshot(window)
            self.assertEqual(len(snap), 1, window)
            self.assertEqual(snap[0]["app"], "Foo", window)
            self.assertEqual(snap[0]["download"], 1_000_000.0, window)

    def test_totals(self):
        store = StatsStore()
        store.record("A", download_kbit=8, upload_kbit=8, now=0)
        store.record("B", download_kbit=16, upload_kbit=0, now=0)
        totals = store.totals("minute")
        self.assertEqual(totals["download"], 1000.0 + 2000.0)
        self.assertEqual(totals["upload"], 1000.0)

    def test_snapshot_sorted_by_total_volume(self):
        store = StatsStore()
        store.record("small", download_kbit=1, now=0)
        store.record("big", download_kbit=100, now=0)
        self.assertEqual([item["app"] for item in store.snapshot("minute")],
                         ["big", "small"])


class RingBufferTest(unittest.TestCase):
    def test_old_minute_buckets_fall_out(self):
        store = StatsStore()
        store.record("old", download_kbit=8, now=0, interval=1)
        # 60 Minuten weiter: der Minute-Ring (60 Buckets) laeuft einmal um.
        store.record("new", download_kbit=8, now=60 * 60, interval=1)
        minute = {item["app"] for item in store.snapshot("minute")}
        self.assertEqual(minute, {"new"})
        # Der Stunden-Ring deckt 48 h ab -> beide bleiben sichtbar.
        hour = {item["app"] for item in store.snapshot("hour")}
        self.assertEqual(hour, {"old", "new"})

    def test_large_time_jump_expires_stale_slots(self):
        store = StatsStore()
        store.record("ancient", download_kbit=8, now=0)
        # Sprung weiter als die gesamte Minute-Ringlaenge ohne Zwischenticks
        store.record("fresh", download_kbit=8, now=3600 * 5)
        minute = {item["app"] for item in store.snapshot("minute")}
        self.assertEqual(minute, {"fresh"})

    def test_invalid_window_rejected(self):
        store = StatsStore()
        with self.assertRaises(ValueError):
            store.snapshot("week")


class PersistenceTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stats.json")
            store = StatsStore(path=path, interval=1.0)
            store.record("Foo", download_kbit=8, upload_kbit=8, now=100)
            store.flush()

            loaded = StatsStore(path=path)
            snap = loaded.snapshot("minute")
            self.assertEqual(len(snap), 1)
            self.assertEqual(snap[0]["app"], "Foo")
            self.assertEqual(snap[0]["download"], 1000.0)
            self.assertEqual(loaded.totals("hour")["upload"], 1000.0)

    def test_config_dir_default_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StatsStore(config_dir=tmp)
            self.assertEqual(store.path, os.path.join(tmp, "stats.json"))

    def test_save_every_throttles_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stats.json")
            store = StatsStore(path=path, save_every=5)
            for i in range(4):
                store.record("Foo", download_kbit=1, now=i)
            self.assertFalse(os.path.exists(path))
            store.record("Foo", download_kbit=1, now=4)
            self.assertTrue(os.path.exists(path))

    def test_corrupt_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stats.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{das ist kein json")
            store = StatsStore(path=path)
            self.assertEqual(store.snapshot("minute"), [])
            self.assertEqual(store.totals("day"), {"download": 0.0, "upload": 0.0})

    def test_wrong_shape_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stats.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "rings": {"minute": []}}, handle)
            store = StatsStore(path=path)
            self.assertEqual(store.snapshot("minute"), [])

    def test_reset_clears_memory_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stats.json")
            store = StatsStore(path=path)
            store.record("Foo", download_kbit=8, now=0)
            store.reset()
            self.assertEqual(store.snapshot("minute"), [])
            reloaded = StatsStore(path=path)
            self.assertEqual(reloaded.snapshot("minute"), [])


if __name__ == "__main__":
    unittest.main()
