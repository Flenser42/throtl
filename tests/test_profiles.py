"""Tests fuer Profile und Zeitplaene (Aufgabe B)."""

import os
import tempfile
import unittest
from datetime import datetime

from throtl import config


class NameValidationTest(unittest.TestCase):
    def test_valid_names(self):
        self.assertEqual(config.validate_profile_name("  Uni  "), "Uni")
        self.assertEqual(config.validate_profile_name("Mein Profil 2024"), "Mein Profil 2024")

    def test_invalid_names(self):
        for bad in ("", "   ", "x" * 65, "Bad/Name", "a!b", None, 42):
            with self.assertRaises(config.ConfigError):
                config.validate_profile_name(bad)


class ParseDaysTest(unittest.TestCase):
    def test_list(self):
        self.assertEqual(config.parse_days(["mo", "di", "mi", "do", "fr"]),
                         {0, 1, 2, 3, 4})

    def test_range(self):
        self.assertEqual(config.parse_days("mo-fr"), {0, 1, 2, 3, 4})
        self.assertEqual(config.parse_days("sa-so"), {5, 6})

    def test_wrapping_range(self):
        self.assertEqual(config.parse_days("fr-mo"), {4, 5, 6, 0})

    def test_english_and_numeric(self):
        self.assertEqual(config.parse_days(["mon", "sunday"]), {0, 6})
        self.assertEqual(config.parse_days([0, 6]), {0, 6})

    def test_unknown_ignored(self):
        self.assertEqual(config.parse_days(["mo", "quatsch"]), {0})
        self.assertEqual(config.parse_days(None), set())


class ProfileCrudTest(unittest.TestCase):
    def _cfg(self):
        cfg = config.default_config()
        cfg["global"]["download_limit"] = 1000
        cfg["processes"] = [config.make_rule("Firefox", "exe", "/usr/lib/firefox/firefox")]
        return cfg

    def test_default_has_standard(self):
        cfg = config.default_config()
        self.assertEqual(cfg["active_profile"], "Standard")
        self.assertEqual(cfg["profiles"], {})
        self.assertEqual(cfg["schedule"], [])
        self.assertEqual(config.profile_names(cfg), ["Standard"])

    def test_capture_and_apply(self):
        cfg = self._cfg()
        config.capture_profile(cfg, "Uni")
        self.assertIn("Uni", config.profile_names(cfg))
        self.assertEqual(cfg["active_profile"], "Uni")

        # Top-Level aendern und ueber das Profil zuruecksetzen.
        cfg["global"]["download_limit"] = 9999
        cfg["processes"] = []
        config.apply_profile(cfg, "Uni")
        self.assertEqual(cfg["active_profile"], "Uni")
        self.assertEqual(cfg["global"]["download_limit"], 1000)
        self.assertEqual(len(cfg["processes"]), 1)

    def test_apply_unknown_raises(self):
        cfg = self._cfg()
        with self.assertRaises(config.ConfigError):
            config.apply_profile(cfg, "GibtEsNicht")

    def test_get_profile_standard_is_live_view(self):
        cfg = self._cfg()
        profile = config.get_profile(cfg, "Standard")
        self.assertEqual(profile["global"]["download_limit"], 1000)
        self.assertEqual(len(profile["processes"]), 1)
        self.assertIsNone(config.get_profile(cfg, "Nope"))

    def test_delete_profile(self):
        cfg = self._cfg()
        config.capture_profile(cfg, "Uni")
        self.assertTrue(config.delete_profile(cfg, "Uni"))
        self.assertFalse(config.delete_profile(cfg, "Uni"))
        self.assertEqual(cfg["active_profile"], "Standard")
        self.assertNotIn("Uni", cfg["profiles"])

    def test_profile_priority_roundtrip_via_dump(self):
        cfg = self._cfg()
        config.capture_profile(cfg, "Uni")
        loaded = self._roundtrip(cfg)
        self.assertEqual(loaded["profiles"]["Uni"]["global"]["download_limit"], 1000)

    def _roundtrip(self, cfg):
        text = config.dump_config(cfg)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            return config.load_config(path)
        finally:
            os.unlink(path)


class ScheduleTest(unittest.TestCase):
    def _cfg(self, days, start, end):
        return config.normalize({
            "schedule": [{"profile": "Uni", "days": days,
                          "start": start, "end": end}],
        })

    def test_daytime_match(self):
        cfg = self._cfg(["mo", "di", "mi", "do", "fr"], "08:00", "14:00")
        # Mittwoch, 10:00
        self.assertEqual(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 7, 10, 0)),
            "Uni")
        # Mittwoch, 15:00 -> ausserhalb
        self.assertIsNone(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 7, 15, 0)))
        # Samstag -> Wochentag passt nicht
        self.assertIsNone(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 10, 10, 0)))

    def test_boundaries_are_half_open(self):
        cfg = self._cfg(["mi"], "08:00", "14:00")
        self.assertEqual(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 7, 8, 0)),
            "Uni")
        self.assertIsNone(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 7, 14, 0)))

    def test_overnight_rule(self):
        cfg = self._cfg(["mo"], "22:00", "06:00")
        # Montag 23:00 -> Abendteil
        self.assertEqual(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 5, 23, 0)),
            "Uni")
        # Dienstag 02:00 -> Morgenteil (Starttag Montag)
        self.assertEqual(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 6, 2, 0)),
            "Uni")
        # Dienstag 23:00 -> nicht mehr
        self.assertIsNone(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 6, 23, 0)))

    def test_first_match_wins(self):
        cfg = config.normalize({
            "schedule": [
                {"profile": "A", "days": ["mi"], "start": "08:00", "end": "14:00"},
                {"profile": "B", "days": ["mi"], "start": "08:00", "end": "14:00"},
            ],
        })
        self.assertEqual(
            config.active_scheduled_profile(cfg, datetime(2026, 1, 7, 10, 0)),
            "A")

    def test_no_schedule(self):
        self.assertIsNone(config.active_scheduled_profile(config.default_config()))


class TomlProfileRoundtripTest(unittest.TestCase):
    def test_full_roundtrip(self):
        cfg = config.default_config()
        cfg["interface"] = "enp34s0"
        cfg["global"]["download_limit"] = 1000
        cfg["processes"] = [config.make_rule("Firefox", "exe",
                                             "/usr/lib/firefox/firefox")]
        config.capture_profile(cfg, "Uni")
        cfg["profiles"]["Uni"]["global"] = {
            "enabled": True,
            "download_limit": 2048,
            "upload_limit": 512,
            "download_priority": "hoch",
            "upload_priority": "hoch",
        }
        cfg["profiles"]["Uni"]["processes"] = [
            config.make_rule("Spotify", "exe", "spotify",
                             download_limit=512, priority="niedrig"),
        ]
        config.capture_profile(cfg, "Mein Profil")
        cfg["start_profile"] = "Uni"
        cfg["schedule"] = config.normalize_schedule([
            {"profile": "Uni", "days": ["mo", "di", "mi", "do", "fr"],
             "start": "08:00", "end": "14:00"},
        ])

        text = config.dump_config(cfg)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            loaded = config.load_config(path)
        finally:
            os.unlink(path)

        self.assertEqual(set(loaded["profiles"]), {"Uni", "Mein Profil"})
        uni = loaded["profiles"]["Uni"]
        self.assertEqual(uni["global"]["download_limit"], 2048)
        self.assertEqual(uni["global"]["upload_limit"], 512)
        self.assertEqual(uni["global"]["download_priority"], "hoch")
        self.assertEqual(len(uni["processes"]), 1)
        self.assertEqual(uni["processes"][0]["name"], "Spotify")
        self.assertEqual(uni["processes"][0]["download_limit"], 512)
        self.assertEqual(loaded["schedule"][0]["profile"], "Uni")
        self.assertEqual(loaded["schedule"][0]["days"], [0, 1, 2, 3, 4])
        self.assertEqual(loaded["schedule"][0]["start"], "08:00")
        self.assertEqual(loaded["start_profile"], "Uni")

    def test_start_profile_bad_name_is_ignored(self):
        cfg = config.normalize({"start_profile": "bad/name!"})
        self.assertIsNone(cfg["start_profile"])
        self.assertIsNone(config.normalize({})["start_profile"])

    def test_v010_config_without_profiles_still_loads(self):
        # Eine v0.1.0-config.toml kennt weder profiles noch schedule.
        raw = """
version = 1
unit = "kbps"

[global]
enabled = true
download_limit = 2048

[[processes]]
key = "exe:/usr/bin/curl"
name = "curl"
match_type = "exe"
match_value = "/usr/bin/curl"
priority = "normal"
recursive = false
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(raw)
            path = handle.name
        try:
            cfg = config.load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(cfg["active_profile"], "Standard")
        self.assertEqual(cfg["profiles"], {})
        self.assertEqual(cfg["schedule"], [])
        self.assertEqual(len(cfg["processes"]), 1)
        self.assertEqual(cfg["processes"][0]["name"], "curl")

    def test_broken_profile_entry_skipped(self):
        data = {
            "profiles": {
                "Gut": {"global_download_limit": 100},
                "": {"global_download_limit": 5},   # leerer Name -> skip
            },
            "schedule": [
                {"profile": "Gut", "days": ["mo"], "start": "08:00", "end": "09:00"},
                {"profile": "Gut", "days": [], "start": "08:00", "end": "09:00"},
                {"profile": "Gut", "days": ["mo"], "start": "kaputt", "end": "09:00"},
            ],
        }
        cfg = config.normalize(data)
        self.assertEqual(set(cfg["profiles"]), {"Gut"})
        self.assertEqual(len(cfg["schedule"]), 1)


class DaemonProfileRpcTest(unittest.TestCase):
    """Profil-RPCs gegen einen echten (Sim-)Daemon."""

    def setUp(self):
        from tests.test_daemon_cli import DaemonHarness, _client

        self._tmp = tempfile.TemporaryDirectory()
        self.harness = DaemonHarness(self._tmp.name)
        self.harness.start()
        self.client = _client(self.harness.socket_path)

    def tearDown(self):
        try:
            self.client.close()
        except Exception:
            pass
        self.harness.stop()
        self._tmp.cleanup()

    def test_profile_lifecycle_via_rpc(self):
        listed = self.client.call("list_profiles")
        self.assertEqual(listed["profiles"], ["Standard"])
        self.assertEqual(listed["active"], "Standard")

        self.client.call("set_global", {"download_limit": "2mbps"})
        saved = self.client.call("set_profile", {"name": "Uni", "activate": True})
        self.assertEqual(saved["active"], "Uni")
        self.assertIn("Uni", self.client.call("list_profiles")["profiles"])

        self.client.call("set_global", {"download_limit": "9mbps"})
        self.client.call("activate_profile", {"name": "Uni"})
        cfg = self.client.call("get_config")
        self.assertEqual(cfg["global"]["download_limit"], 2000)
        self.assertEqual(cfg["active_profile"], "Uni")

        self.client.call("set_schedule", {"rules": [
            {"profile": "Uni", "days": ["mi"], "start": "08:00", "end": "14:00"},
        ]})
        self.assertEqual(len(self.client.call("get_config")["schedule"]), 1)

        deleted = self.client.call("delete_profile", {"name": "Uni"})
        self.assertTrue(deleted["deleted"])

    def test_start_profile_rpc(self):
        self.client.call("set_global", {"download_limit": "5mbps"})
        self.client.call("set_profile", {"name": "Morgen", "activate": True})
        # Vom Profil abweichen und Start-Profil setzen.
        self.client.call("set_global", {"download_limit": "9mbps"})
        self.client.call("set_start_profile", {"name": "Morgen"})
        self.assertEqual(
            self.client.call("get_config")["start_profile"], "Morgen")

    def test_import_config_rpc(self):
        result = self.client.call("import_config", {"config": {
            "unit": "mBs",
            "global": {"download_limit": 1234},
            "processes": [{"name": "curl", "match_type": "name",
                           "match_value": "curl", "priority": "hoch"}],
        }})
        self.assertEqual(result["global"]["download_limit"], 1234)
        cfg = self.client.call("get_config")
        self.assertEqual(cfg["unit"], "mBs")
        self.assertEqual(len(cfg["processes"]), 1)


class StartProfileApplyTest(unittest.TestCase):
    """Ein gesetztes start_profile wird beim Daemon-Start aktiviert."""

    def test_start_profile_applied_on_daemon_start(self):
        from throtl import daemon
        from throtl.engine import SimEngine

        with tempfile.TemporaryDirectory() as tmp:
            first = daemon.Daemon(
                socket_path=os.path.join(tmp, "d.sock"), config_dir=tmp,
                engine=SimEngine("lo"), monitor_factory=None)
            cfg = first.store.get()
            cfg["global"]["download_limit"] = 4321
            config.capture_profile(cfg, "Morgen")
            # Danach abweichen und "Standard" aktiv lassen: der Neustart muss
            # das Start-Profil anwenden.
            cfg["global"]["download_limit"] = 9999
            cfg["active_profile"] = config.STANDARD_PROFILE
            cfg["start_profile"] = "Morgen"
            first.store._persist()

            second = daemon.Daemon(
                socket_path=os.path.join(tmp, "d2.sock"), config_dir=tmp,
                engine=SimEngine("lo"), monitor_factory=None)
            second._apply_start_profile()
            loaded = second.store.get()
            self.assertEqual(loaded["active_profile"], "Morgen")
            self.assertEqual(loaded["global"]["download_limit"], 4321)


if __name__ == "__main__":
    unittest.main()
