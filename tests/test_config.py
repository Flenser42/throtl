import os
import tempfile
import unittest

from throtl import config


class DefaultConfigTest(unittest.TestCase):
    def test_structure(self):
        cfg = config.default_config()
        self.assertEqual(cfg["version"], 1)
        self.assertIsNone(cfg["interface"])
        self.assertTrue(cfg["global"]["enabled"])
        self.assertIsNone(cfg["global"]["download_limit"])
        self.assertEqual(cfg["global"]["download_minimum"], 100)
        self.assertEqual(cfg["processes"], [])


class PriorityTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(config.priority_to_int("kritisch"), 0)
        self.assertEqual(config.priority_to_int("hoch"), 1)
        self.assertEqual(config.priority_to_int("normal"), 2)
        self.assertEqual(config.priority_to_int("niedrig"), 3)

    def test_ints(self):
        self.assertEqual(config.priority_to_int(0), 0)
        self.assertEqual(config.priority_to_int(3), 3)

    def test_invalid(self):
        for bad in ("ultra", 7, -1, None):
            with self.assertRaises(config.ConfigError):
                config.priority_to_int(bad)

    def test_to_name(self):
        self.assertEqual(config.priority_to_name("kritisch"), "kritisch")
        self.assertEqual(config.priority_to_name(0), "kritisch")
        self.assertEqual(config.priority_to_name(99), "normal")


class RuleTest(unittest.TestCase):
    def test_make_rule_escapes_exe(self):
        rule = config.make_rule("X", "exe", "/opt/foo+bar/app")
        self.assertEqual(rule["match_value"], "/opt/foo\\+bar/app")
        self.assertEqual(rule["key"], "exe:/opt/foo\\+bar/app")

    def test_make_rule_name_not_escaped_twice(self):
        rule = config.make_rule("X", "name", "firefox")
        self.assertEqual(rule["match_value"], "firefox")
        self.assertEqual(rule["priority"], "normal")
        self.assertFalse(rule["recursive"])

    def test_make_rule_validates(self):
        with self.assertRaises(config.ConfigError):
            config.make_rule("X", "port", "80")
        with self.assertRaises(config.ConfigError):
            config.make_rule("X", "exe", "  ")

    def test_cmdline_kept_as_regex(self):
        rule = config.make_rule("JD", "cmdline", ".* JDownloader\\.jar")
        self.assertEqual(rule["match_value"], ".* JDownloader\\.jar")


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.processes = [
            config.make_rule("Firefox", "exe", "/usr/lib/firefox/firefox"),
            config.make_rule("Steam", "name", "steam"),
            config.make_rule("JD", "cmdline", ".* JDownloader"),
        ]

    def test_exe_match(self):
        hits = config.matching_rules(self.processes, exe="/usr/lib/firefox/firefox")
        self.assertEqual([r["name"] for r in hits], ["Firefox"])

    def test_name_match(self):
        hits = config.matching_rules(self.processes, name="steam")
        self.assertEqual([r["name"] for r in hits], ["Steam"])

    def test_cmdline_match(self):
        hits = config.matching_rules(self.processes, cmdline="/usr/bin/java -jar JDownloader.jar")
        self.assertEqual([r["name"] for r in hits], ["JD"])

    def test_no_match(self):
        self.assertEqual(config.matching_rules(self.processes, exe="/usr/bin/foo"), [])

    def test_exe_path_with_metachars(self):
        rule = config.make_rule("X", "exe", "/usr/lib/foo+bar/baz")
        hits = config.matching_rules([rule], exe="/usr/lib/foo+bar/baz")
        self.assertEqual(len(hits), 1)


class TomlRoundtripTest(unittest.TestCase):
    def _roundtrip(self, cfg):
        text = config.dump_config(cfg)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
            path = handle.name
        try:
            return config.load_config(path)
        finally:
            os.unlink(path)

    def test_full_roundtrip(self):
        cfg = config.default_config()
        cfg["interface"] = "enp34s0"
        cfg["unit"] = "kBs"
        cfg["global"]["download_limit"] = 10_000
        cfg["global"]["upload_limit"] = None
        cfg["global"]["download_priority"] = "hoch"
        cfg["processes"] = [
            config.make_rule("Firefox", "exe", "/usr/lib/firefox/firefox",
                             download_limit=2048, upload_limit=512, priority="kritisch"),
            config.make_rule("Steam", "name", "steam", recursive=True),
        ]
        loaded = self._roundtrip(cfg)
        self.assertEqual(loaded["interface"], "enp34s0")
        self.assertEqual(loaded["unit"], "kBs")
        self.assertEqual(loaded["global"]["download_limit"], 10_000)
        self.assertIsNone(loaded["global"]["upload_limit"])
        self.assertEqual(loaded["global"]["download_priority"], "hoch")
        self.assertEqual(len(loaded["processes"]), 2)
        first, second = loaded["processes"]
        self.assertEqual(first["name"], "Firefox")
        self.assertEqual(first["match_value"], "/usr/lib/firefox/firefox")
        self.assertEqual(first["download_limit"], 2048)
        self.assertEqual(first["priority"], "kritisch")
        self.assertFalse(first["recursive"])
        self.assertEqual(second["recursive"], True)
        self.assertEqual(second["match_type"], "name")

    def test_escaping_roundtrip(self):
        cfg = config.default_config()
        rule = config.make_rule("Weird", "exe", '/opt/quote"back\\slash/äpp')
        cfg["processes"] = [rule]
        loaded = self._roundtrip(cfg)
        # TOML-Roundtrip muss die regex-gescapeten Werte 1:1 erhalten
        self.assertEqual(loaded["processes"][0]["match_value"], rule["match_value"])

    def test_dump_omits_unset_limits(self):
        text = config.dump_config(config.default_config())
        self.assertNotIn("download_limit", text)

    def test_load_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load_config(os.path.join(tmp, "nope.toml"))
        self.assertEqual(cfg, config.default_config())

    def test_normalize_rejects_bad_priority(self):
        with self.assertRaises(config.ConfigError):
            config.normalize({"global": {"download_priority": "ludicrous"}})

    def test_normalize_rejects_bad_unit(self):
        with self.assertRaises(config.ConfigError):
            config.normalize({"unit": "tb"})

    def test_normalize_skips_broken_rule(self):
        cfg = config.normalize({"processes": [{"match_type": "exe"}]})
        self.assertEqual(cfg["processes"], [])


class InterfaceDetectionTest(unittest.TestCase):
    def test_returns_string_or_none(self):
        result = config.detect_default_interface()
        self.assertTrue(result is None or isinstance(result, str))


class ConfigPathTest(unittest.TestCase):
    def test_default_dir(self):
        self.assertTrue(config.config_dir_default().endswith("netlimiter-clone"))

    def test_env_override(self):
        import os

        os.environ["THROTL_CONFIG_DIR"] = "/tmp/throtl-test-config"
        try:
            self.assertEqual(config.config_dir_default(), "/tmp/throtl-test-config")
        finally:
            del os.environ["THROTL_CONFIG_DIR"]


if __name__ == "__main__":
    unittest.main()
