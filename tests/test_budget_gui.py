"""Budget-Editor der GUI: Anzeigeform, Formatierung und RPC-Aufrufe.

Die reinen Funktionen (``budget_rows``, ``format_volume``) laufen ohne
PyGObject und damit auch auf den Lint-/Test-Runnern im CI; nur die
Widget-Tests brauchen ein Display.
"""

import unittest

PAYLOAD = {
    "enabled": True,
    "entries": [
        {"scope": "global", "app": None, "window": "day",
         "used": 3_500_000_000, "limit": 20_000_000_000, "ratio": 0.175,
         "exceeded": False},
        {"scope": "global", "app": None, "window": "week",
         "used": 40_200_000_000, "limit": 100_000_000_000, "ratio": 0.402,
         "exceeded": False},
        {"scope": "app", "app": "firefox", "window": "day",
         "used": 5_100_000_000, "limit": 5_000_000_000, "ratio": 1.02,
         "exceeded": True},
        {"scope": "app", "app": "steam", "window": "week",
         "used": 1_000, "limit": 50_000_000_000, "ratio": 0.0,
         "exceeded": False},
    ],
}


def _display_available():
    """GTK muss initialisiert sein, sonst ist Gdk.Display immer None."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        Gtk.init_check()
        from gi.repository import Gdk

        return Gdk.Display.get_default() is not None
    except Exception:
        return False


class BudgetRowsTest(unittest.TestCase):
    """``get_budgets`` in Anzeigeform bringen (ohne Widgets)."""

    def test_splits_global_and_app_entries_by_window(self):
        from throtl.budgets import budget_rows

        rows = budget_rows(PAYLOAD)
        self.assertTrue(rows["enabled"])
        self.assertEqual(rows["global"]["day"]["limit"], 20_000_000_000)
        self.assertEqual(rows["global"]["week"]["used"], 40_200_000_000)
        self.assertEqual([a["app"] for a in rows["apps"]], ["firefox", "steam"])
        self.assertEqual(rows["apps"][1]["week"]["limit"], 50_000_000_000)

    def test_unset_window_is_none_not_zero(self):
        from throtl.budgets import budget_rows

        rows = budget_rows({"enabled": True, "entries": [
            {"scope": "global", "app": None, "window": "day",
             "used": 0, "limit": 1000, "ratio": 0.0, "exceeded": False}]})
        self.assertIsNone(rows["global"]["week"])
        self.assertEqual(rows["apps"], [])

    def test_entries_without_usable_window_are_ignored(self):
        from throtl.budgets import budget_rows

        rows = budget_rows({"enabled": True, "entries": [
            {"scope": "app", "app": "x", "window": "month", "limit": 5},
            {"scope": "app", "app": "", "window": "day", "limit": 5}]})
        self.assertEqual(rows["apps"], [])

    def test_disabled_state_is_reported(self):
        from throtl.budgets import budget_rows

        rows = budget_rows({"enabled": False, "entries": []})
        self.assertFalse(rows["enabled"])


class FormatVolumeTest(unittest.TestCase):
    """Volumen fuer Eingabefelder formatieren und zurueckparsen."""

    def test_formats_without_trailing_zero(self):
        from throtl.budgets import format_volume

        self.assertEqual(format_volume(20_000_000_000), "20 GB")
        self.assertEqual(format_volume(1_500_000), "1.5 MB")
        self.assertEqual(format_volume(0), "0 B")

    def test_none_means_no_budget(self):
        from throtl.budgets import format_volume

        self.assertEqual(format_volume(None), "")

    def test_round_trips_through_parse_size(self):
        from throtl.budgets import format_volume
        from throtl.units import parse_size

        for value in (20_000_000_000, 500_000_000, 1_500_000):
            self.assertEqual(parse_size(format_volume(value)), value)


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class BudgetDialogTest(unittest.TestCase):
    """Widget-Verhalten: Vorbelegung, Speichern, Entfernen, Validierung."""

    def _dialog(self, payload=None):
        from throtl.gui.budget_dialog import BudgetDialog

        calls = []

        class _Gui:
            def call(self, method, params=None, timeout=10):
                if method == "get_budgets":
                    return payload or {"enabled": True, "entries": []}
                return {}

            def call_async(self, method, params=None, **kwargs):
                calls.append((method, params))
                if kwargs.get("on_done"):
                    kwargs["on_done"]({})

        return BudgetDialog(None, _Gui()), calls

    def test_prefills_the_configured_limits(self):
        dialog, _ = self._dialog(PAYLOAD)
        try:
            self.assertEqual(dialog.global_day.get_text(), "20 GB")
            self.assertEqual(dialog.global_week.get_text(), "100 GB")
            self.assertEqual([row.app for row in dialog.app_rows],
                             ["firefox", "steam"])
        finally:
            dialog.force_close()

    def test_saving_global_sends_parsed_bytes(self):
        dialog, calls = self._dialog(PAYLOAD)
        try:
            dialog.global_day.set_text("30 GB")
            dialog._save_global()
            self.assertIn(("set_budget", {"day": 30_000_000_000,
                                          "week": 100_000_000_000}), calls)
        finally:
            dialog.force_close()

    def test_clearing_a_field_removes_that_window(self):
        dialog, calls = self._dialog(PAYLOAD)
        try:
            dialog.global_week.set_text("")
            dialog._save_global()
            self.assertIn(("set_budget", {"day": 20_000_000_000,
                                          "week": None}), calls)
        finally:
            dialog.force_close()

    def test_invalid_input_is_rejected_before_the_rpc(self):
        dialog, calls = self._dialog(PAYLOAD)
        try:
            dialog.global_day.set_text("abc")
            dialog._save_global()
            self.assertEqual([c for c in calls if c[0] == "set_budget"], [])
            self.assertIn("abc", dialog.error_banner.get_title())
        finally:
            dialog.force_close()

    def test_removing_an_app_budget(self):
        dialog, calls = self._dialog(PAYLOAD)
        try:
            dialog._remove_app("firefox")
            self.assertIn(("remove_budget", {"app": "firefox"}), calls)
        finally:
            dialog.force_close()

    def test_switch_disables_budgets(self):
        dialog, calls = self._dialog(PAYLOAD)
        try:
            dialog.enabled_switch.set_active(False)
            dialog._on_enabled(dialog.enabled_switch)
            self.assertIn(("set_budget", {"enabled": False}), calls)
        finally:
            dialog.force_close()

    def test_empty_state_explains_what_a_budget_does(self):
        dialog, _ = self._dialog({"enabled": True, "entries": []})
        try:
            self.assertEqual(dialog.app_rows, [])
            self.assertIsNotNone(dialog.empty_row)
            self.assertIn("budget", dialog.empty_row.get_subtitle().lower())
        finally:
            dialog.force_close()

    def test_configured_apps_replace_the_empty_state(self):
        dialog, _ = self._dialog(PAYLOAD)
        try:
            self.assertIsNone(dialog.empty_row.get_parent())
        finally:
            dialog.force_close()

    def test_adding_an_app_budget(self):
        dialog, calls = self._dialog({"enabled": True, "entries": []})
        try:
            dialog._add_app("firefox", "5 GB", "")
            self.assertIn(("set_budget", {"app": "firefox",
                                          "day": 5_000_000_000}), calls)
        finally:
            dialog.force_close()

    def test_adding_without_any_limit_is_rejected(self):
        dialog, calls = self._dialog({"enabled": True, "entries": []})
        try:
            dialog._add_app("firefox", "", "")
            self.assertEqual([c for c in calls if c[0] == "set_budget"], [])
            self.assertIn("limit", dialog.error_banner.get_title().lower())
        finally:
            dialog.force_close()


if __name__ == "__main__":
    unittest.main()
