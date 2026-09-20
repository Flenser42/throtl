"""CLI-Helfer und Guards (kein Daemon noetig)."""

import argparse
import unittest

from throtl import cli


class _FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}

    def call(self, method, params=None, timeout=10):
        return self.responses.get(method, {})


class HelpersTest(unittest.TestCase):
    def test_fmt_bytes(self):
        self.assertEqual(cli._fmt_bytes(0), "0.0 B")
        self.assertEqual(cli._fmt_bytes(1500), "1.5 KB")
        self.assertEqual(cli._fmt_bytes(2_500_000), "2.5 MB")

    def test_proc_display(self):
        self.assertEqual(cli._proc_display("/usr/bin/curl -s -o /dev/null"), "curl")
        self.assertEqual(cli._proc_display("/opt/a/b"), "b")
        long = cli._proc_display("/x/" + "a" * 50, limit=10)
        self.assertEqual(len(long), 10)
        self.assertTrue(long.endswith("…"))

    def test_find_app(self):
        state = {"apps": [{"name": "curl", "download": 5.0},
                          {"name": "firefox", "download": 1.0}]}
        self.assertEqual(cli._find_app(state, "curl")["name"], "curl")
        self.assertIsNone(cli._find_app(state, "wget"))


class SelfTestGuardTest(unittest.TestCase):
    def _args(self):
        return argparse.Namespace(limit="2mbps", url="http://x", time=6.0,
                                  warmup=4.0, measure=5.0)

    def test_simulated_daemon_is_rejected(self):
        rc = cli.cmd_selftest(_FakeClient({"status": {"simulated": True}}), self._args())
        self.assertEqual(rc, 2)

    def test_disabled_shaping_is_rejected(self):
        status = {"simulated": False, "enabled": False, "monitoring": True}
        rc = cli.cmd_selftest(_FakeClient({"status": status}), self._args())
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
