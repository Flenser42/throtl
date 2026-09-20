"""GUI-Prefs (Sortier-Persistenz) — reine Logik, kein Display noetig."""

import os
import tempfile
import unittest

from throtl.gui import prefs


class PrefsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self._tmp.cleanup()

    def test_roundtrip(self):
        self.assertEqual(prefs.load_prefs(), {})
        prefs.save_prefs({"sort_key": "upload", "sort_desc": False})
        self.assertEqual(prefs.load_prefs(),
                         {"sort_key": "upload", "sort_desc": False})

    def test_corrupt_file_is_ignored(self):
        path = prefs.prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(prefs.load_prefs(), {})

    def test_save_is_best_effort(self):
        # Ein nicht beschreibbarer Pfad darf nicht werfen.
        os.environ["XDG_CONFIG_HOME"] = "/proc/definitely-not-writable"
        prefs.save_prefs({"a": 1})  # darf nicht raisen


if __name__ == "__main__":
    unittest.main()
