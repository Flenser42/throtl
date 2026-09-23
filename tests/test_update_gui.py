"""GUI-Verhalten des Versionschecks: Banner, Knopf, Dismiss, Schalter."""

import os
import tempfile
import unittest


def _display_available():
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        Gtk.init_check()
        from gi.repository import Gdk

        return Gdk.Display.get_default() is not None
    except Exception:
        return False


class _Gui:
    connected = True

    def call(self, method, params=None, timeout=10):
        return {}

    def call_async(self, method, params=None, **kwargs):
        pass

    def shutdown(self):
        pass


@unittest.skipUnless(_display_available(), "kein GTK-Display verfuegbar")
class UpdateBannerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._tmp.cleanup()

    def _window(self):
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        from throtl.gui.app import ThrotlWindow

        app = Adw.Application(application_id=f"io.github.throtl.upd{id(self)}")
        app.register(None)
        opened = []
        win = ThrotlWindow(app, _Gui())
        win._open_uri = lambda _parent, uri: opened.append(uri)
        win._opened = opened
        return win

    def test_banner_names_both_versions_and_offers_download(self):
        from throtl import __version__

        win = self._window()
        try:
            win._on_update_available("99.0.0")
            self.assertTrue(win.banner.get_revealed())
            self.assertIn("99.0.0", win.banner.get_title())
            self.assertIn(__version__, win.banner.get_title())
            self.assertEqual(win.banner.get_button_label(), "Download")
        finally:
            win.destroy()

    def test_download_opens_the_release_page(self):
        from throtl.version import RELEASES_URL

        win = self._window()
        try:
            win._on_update_available("99.0.0")
            win._on_banner_button()
            self.assertTrue(win._opened)
            self.assertTrue(win._opened[0].startswith(RELEASES_URL))
            self.assertFalse(win.banner.get_revealed())
        finally:
            win.destroy()

    def test_same_version_is_not_reported(self):
        from throtl import __version__

        win = self._window()
        try:
            win._on_update_available(__version__)
            self.assertFalse(win.banner.get_revealed())
        finally:
            win.destroy()

    def test_dismissed_version_does_not_come_back(self):
        win = self._window()
        try:
            win._on_update_available("99.0.0")
            win._on_banner_button()
            win.banner.set_revealed(False)
            self.assertFalse(win.banner.get_revealed())
            self.assertEqual(win._prefs.get("dismissed_update"), "99.0.0")
            win._on_update_available("99.0.0")
            self.assertFalse(win.banner.get_revealed())
        finally:
            win.destroy()

    def test_manual_check_ignores_the_dismissal(self):
        win = self._window()
        try:
            win._on_update_available("99.0.0")
            win._on_banner_button()
            win._on_update_available("99.0.0", force=True)
            self.assertTrue(win.banner.get_revealed())
        finally:
            win.destroy()

    def test_auto_check_can_be_switched_off(self):
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        win = self._window()
        try:
            self.assertTrue(win._should_check(manual=False))
            action = win.lookup_action("check-updates-on-start")
            self.assertIsNotNone(action)
            action.activate(GLib.Variant.new_boolean(False))
            self.assertFalse(win._should_check(manual=False))
            self.assertFalse(win._prefs.get("check_updates", True))
            # Manuell bleibt immer moeglich.
            self.assertTrue(win._should_check(manual=True))
        finally:
            win.destroy()

    def test_manual_check_action_exists(self):
        win = self._window()
        try:
            self.assertIsNotNone(win.lookup_action("check-updates"))
        finally:
            win.destroy()


if __name__ == "__main__":
    unittest.main()
