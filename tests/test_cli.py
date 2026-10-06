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


class _SeqClient:
    """Gibt bei jedem ``list_processes`` den naechsten Zustand zurueck."""

    def __init__(self, states):
        self._states = list(states)
        self._index = 0

    def call(self, method, params=None, timeout=10):
        if method != "list_processes":
            return {}
        if not self._states:
            return {"apps": []}
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1
        return state


class WatchCommandTest(unittest.TestCase):
    def _args(self, **overrides):
        base = dict(duration=1.0, interval=0.2, app=None, alert=None, json=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_report_and_no_alert(self):
        import contextlib
        import io

        states = [
            {"interface": "eth0",
             "apps": [{"name": "firefox", "download": 1000.0, "upload": 100.0}]},
            {"interface": "eth0",
             "apps": [{"name": "firefox", "download": 2000.0, "upload": 300.0}]},
        ]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_watch(_SeqClient(states), self._args(alert="10mbps"))
        self.assertEqual(rc, 0)
        self.assertIn("firefox", buf.getvalue())
        self.assertIn("OK: no app exceeded", buf.getvalue())

    def test_alert_triggers_exit_4(self):
        import contextlib
        import io

        states = [{"interface": "eth0",
                   "apps": [{"name": "game", "download": 50000.0, "upload": 0.0}]}]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_watch(_SeqClient(states), self._args(alert="1mbps"))
        self.assertEqual(rc, 4)
        self.assertIn("ALERT", buf.getvalue())

    def test_json_output(self):
        import contextlib
        import io
        import json

        states = [{"interface": "eth0",
                   "apps": [{"name": "curl", "download": 800.0, "upload": 20.0}]}]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_watch(_SeqClient(states), self._args(json=True))
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["apps"][0]["name"], "curl")
        self.assertEqual(payload["interface"], "eth0")

    def test_invalid_alert_value(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_watch(_SeqClient([]), self._args(alert="nonsense"))
        self.assertEqual(rc, 2)

    def test_invalid_interval_is_rejected(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.cmd_watch(_SeqClient([]), self._args(interval="abc"))
        self.assertEqual(rc, 2)


class MainConnectionErrorTest(unittest.TestCase):
    def test_daemon_loss_is_reported_not_raised(self):
        import contextlib
        import io
        from unittest import mock

        class Client:
            def call(self, *args, **kwargs):
                raise ConnectionError("daemon went away")

            def close(self):
                pass

        err = io.StringIO()
        with mock.patch.object(cli, "_client", return_value=Client()), \
             contextlib.redirect_stderr(err):
            rc = cli.main(["status"])
        self.assertEqual(rc, 1)
        self.assertIn("connection", err.getvalue())


class TopCommandTest(unittest.TestCase):
    def test_invalid_interval_is_rejected(self):
        import argparse
        import io
        from unittest import mock

        class _Tty:
            def isatty(self):
                return True

            def write(self, text):
                pass

            def flush(self):
                pass

        args = argparse.Namespace(interval="abc", sort="download")
        with mock.patch.object(cli.sys, "stdout", _Tty()), \
             mock.patch.object(cli.sys, "stderr", io.StringIO()):
            rc = cli.cmd_top(_FakeClient({}), args)
        self.assertEqual(rc, 2)


class SelfTestGuardTest(unittest.TestCase):
    def _args(self, **overrides):
        base = dict(limit="2mbps", url="http://x", time=6.0,
                    warmup=4.0, measure=5.0)
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_simulated_daemon_is_rejected(self):
        rc = cli.cmd_selftest(_FakeClient({"status": {"simulated": True}}), self._args())
        self.assertEqual(rc, 2)

    def test_disabled_shaping_is_rejected(self):
        status = {"simulated": False, "enabled": False, "monitoring": True}
        rc = cli.cmd_selftest(_FakeClient({"status": status}), self._args())
        self.assertEqual(rc, 2)

    def test_invalid_limit_is_rejected(self):
        from unittest import mock

        status = {"simulated": False, "enabled": True, "monitoring": True}
        with mock.patch("shutil.which", return_value="/usr/bin/curl"):
            rc = cli.cmd_selftest(_FakeClient({"status": status}),
                                  self._args(limit="garbage"))
        self.assertEqual(rc, 2)


class SelfTestCleanupTest(unittest.TestCase):
    """Der Selftest darf keine Drossel-Regel hinterlassen."""

    def _args(self):
        return argparse.Namespace(limit="2mbps", url="http://x", time=1.0,
                                  warmup=0.0, measure=1)

    def test_removes_its_rule_when_existing_key_differs(self):
        import contextlib
        import io
        from unittest import mock

        calls = []

        class Client:
            def call(self, method, params=None, timeout=10):
                calls.append((method, params))
                if method == "status":
                    return {"simulated": False, "enabled": True, "monitoring": True}
                if method == "get_config":
                    return {"processes": [
                        {"name": "curl", "match_type": "exe",
                         "match_value": "curl", "key": "exe:curl"}]}
                if method == "set_process":
                    return {"key": "exe:/usr/bin/curl",
                            "match_value": "/usr/bin/curl"}
                if method == "list_processes":
                    return {"apps": [{"name": "curl", "download": 100.0}]}
                return {}

        measured = mock.Mock()
        measured.stdout = "100000"
        with mock.patch("shutil.which", return_value="/usr/bin/curl"), \
             mock.patch("subprocess.run", return_value=measured), \
             mock.patch("subprocess.Popen", return_value=mock.Mock()), \
             mock.patch.object(cli.time, "sleep", return_value=None), \
             mock.patch.object(cli, "_wait_engine", return_value=None), \
             contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_selftest(Client(), self._args())

        removed = [str(p.get("key")) for m, p in calls if m == "remove_process"]
        self.assertIn("exe:/usr/bin/curl", removed)


if __name__ == "__main__":
    unittest.main()
