"""Erklaerzeile einer Tabellenzeile: warum diese App gerade so laeuft.

Rein und ohne Widgets, damit die Erklaerung ohne Display und im CI pruefbar
bleibt. Die Zeile beantwortet die Frage, die sonst nur das README beantwortet:
globale Limits, eigene Regel, Zeitfenster, aktives Profil — was gilt jetzt?
"""

import unittest
from datetime import datetime

# Donnerstag, 12:00 / 21:00 — das Fenster unten gilt Mo-Fr 20:00-00:00.
NOON = datetime(2026, 9, 24, 12, 0)
EVENING = datetime(2026, 9, 24, 21, 0)

WINDOW = {"days": [0, 1, 2, 3, 4], "start": "20:00", "end": "00:00"}


class ExplainTest(unittest.TestCase):
    def test_nothing_applies_means_no_line(self):
        from throtl.rowinfo import explain

        self.assertIsNone(explain(None, {"download_limit": None,
                                        "upload_limit": None}, "mBs", NOON))

    def test_own_limits_are_named_in_the_current_unit(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": 4000, "upload_limit": 500,
                "priority": "hoch", "window": None}
        text = explain(rule, {}, "mBs", NOON)
        self.assertIn("0.5 MB/s", text)
        self.assertIn("download", text.lower())
        self.assertIn("upload", text.lower())

    def test_one_sided_limit_does_not_invent_the_other(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": 4000, "upload_limit": None,
                "priority": "normal", "window": None}
        text = explain(rule, {}, "mBs", NOON)
        self.assertIn("0.5 MB/s", text)
        self.assertNotIn("upload", text.lower())

    def test_inactive_window_is_stated_as_inactive(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": 4000, "upload_limit": None,
                "priority": "normal", "window": WINDOW}
        text = explain(rule, {}, "mBs", NOON)
        self.assertIn("20:00-00:00", text)
        self.assertIn("not active", text.lower())

    def test_active_window_is_stated_as_active(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": 4000, "upload_limit": None,
                "priority": "normal", "window": WINDOW}
        text = explain(rule, {}, "mBs", EVENING)
        self.assertIn("active now", text.lower())
        self.assertNotIn("not active", text.lower())

    def test_global_limit_is_credited_to_the_global_setting(self):
        from throtl.rowinfo import explain

        text = explain(None, {"download_limit": 80000, "upload_limit": None},
                       "mBs", NOON)
        self.assertIn("global", text.lower())
        self.assertIn("10 MB/s", text)

    def test_own_rule_wins_over_the_global_sentence(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": 4000, "upload_limit": None,
                "priority": "normal", "window": None}
        text = explain(rule, {"download_limit": 80000, "upload_limit": None},
                       "mBs", NOON)
        self.assertIn("0.5 MB/s", text)
        self.assertNotIn("10 MB/s", text)

    def test_window_without_limit_is_still_explained(self):
        """Ein Fenster ohne Limit sagt trotzdem, wann die Regel gilt."""
        from throtl.rowinfo import explain

        rule = {"download_limit": None, "upload_limit": None,
                "priority": "hoch", "window": WINDOW}
        text = explain(rule, {}, "mBs", NOON)
        self.assertIsNotNone(text)
        self.assertIn("20:00-00:00", text)

    def test_priority_alone_is_worth_a_line(self):
        """Eine gesetzte Prioritaet ist eine Aussage — auch ohne Limit."""
        from throtl.rowinfo import explain

        rule = {"download_limit": None, "upload_limit": None,
                "priority": "hoch", "window": None}
        text = explain(rule, {}, "mBs", NOON)
        self.assertIsNotNone(text)
        self.assertIn("High", text)

    def test_normal_priority_alone_stays_silent(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": None, "upload_limit": None,
                "priority": "normal", "window": None}
        self.assertIsNone(explain(rule, {}, "mBs", NOON))

    def test_priority_is_appended_to_limits(self):
        from throtl.rowinfo import explain

        rule = {"download_limit": 4000, "upload_limit": None,
                "priority": "niedrig", "window": None}
        text = explain(rule, {}, "mBs", NOON)
        self.assertIn("0.5 MB/s", text)
        self.assertIn("Low", text)


if __name__ == "__main__":
    unittest.main()
