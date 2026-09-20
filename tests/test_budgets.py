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


if __name__ == "__main__":
    unittest.main()
