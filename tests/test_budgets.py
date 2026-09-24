"""Verbrauchs-Budgets (Punkt 3): rollierende day/week-Schwellen."""

import unittest

from throtl.budgets import budget_status
from throtl.stats import StatsStore


class BudgetStatusTest(unittest.TestCase):
    def test_disabled_returns_nothing(self):
        store = StatsStore()
        cfg = {"budgets": {"enabled": False, "day": 1, "week": 1, "rules": []}}
        self.assertEqual(budget_status(cfg, store), [])

    def test_global_day_budget(self):
        store = StatsStore()
        # 1000 kbit/s fuer eine Stunde = 450 MB in den Stundenbucket.
        store.record("firefox", download_kbit=1000, now=1000.0, interval=3600.0)
        cfg = {"budgets": {"enabled": True, "day": 100_000_000, "week": None,
                           "rules": []}}
        entries = budget_status(cfg, store, now=1060.0)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["scope"], "global")
        self.assertEqual(entry["window"], "day")
        self.assertGreater(entry["used"], 100_000_000)
        self.assertTrue(entry["exceeded"])
        self.assertGreater(entry["ratio"], 1.0)

    def test_app_week_budget_uses_day_buckets(self):
        store = StatsStore()
        store.record("steam", download_kbit=8000, now=0.0, interval=86400.0)
        cfg = {"budgets": {"enabled": True, "day": None, "week": None,
                           "rules": [{"app": "steam", "day": None, "week": 10_000}]}}
        entries = budget_status(cfg, store, now=60.0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["app"], "steam")
        self.assertEqual(entries[0]["window"], "week")
        self.assertTrue(entries[0]["exceeded"])

    def test_not_exceeded(self):
        store = StatsStore()
        store.record("curl", download_kbit=1, now=0.0, interval=1.0)
        cfg = {"budgets": {"enabled": True, "day": 10 ** 12, "week": None,
                           "rules": []}}
        entries = budget_status(cfg, store, now=1.0)
        self.assertFalse(entries[0]["exceeded"])


class BudgetWarningTest(unittest.TestCase):
    """Vorwarnung, bevor das Volumen weg ist (Schwelle 80 %)."""

    @staticmethod
    def _entry(ratio, exceeded=False, app="steam"):
        return {"scope": "app", "app": app, "window": "day",
                "used": ratio * 100, "limit": 100, "ratio": ratio,
                "exceeded": exceeded}

    def test_warns_above_the_threshold(self):
        from throtl.budgets import pending_warnings

        warnings = pending_warnings([self._entry(0.82)], set())
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["app"], "steam")

    def test_stays_quiet_below_the_threshold(self):
        from throtl.budgets import pending_warnings

        self.assertEqual(pending_warnings([self._entry(0.5)], set()), [])

    def test_exceeded_entries_are_not_warned_about(self):
        """Ueberschritten meldet der bestehende Pfad — nicht doppelt."""
        from throtl.budgets import pending_warnings

        self.assertEqual(
            pending_warnings([self._entry(1.1, exceeded=True)], set()), [])

    def test_already_warned_is_not_repeated(self):
        from throtl.budgets import pending_warnings, warning_key

        entry = self._entry(0.82)
        warned = {warning_key(entry)}
        self.assertEqual(pending_warnings([entry], warned), [])

    def test_key_separates_scope_app_and_window(self):
        from throtl.budgets import warning_key

        self.assertNotEqual(
            warning_key({"scope": "app", "app": "steam", "window": "day"}),
            warning_key({"scope": "app", "app": "steam", "window": "week"}))


if __name__ == "__main__":
    unittest.main()
