"""Versionscheck gegen die oeffentliche GitHub-Release-API.

Reine Logik plus ein injizierbarer HTTP-Opener: die Tests laufen damit ohne
Netzwerk, und die GUI kann den Check im Hintergrund ausfuehren, ohne dass hier
GTK importiert wird.
"""

import unittest


class _Response:
    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class ParseTagTest(unittest.TestCase):
    def test_strips_the_version_prefix(self):
        from throtl.version import parse_tag

        self.assertEqual(parse_tag({"tag_name": "v0.9.0"}), "0.9.0")
        self.assertEqual(parse_tag({"tag_name": "0.9.0"}), "0.9.0")

    def test_missing_or_broken_payload_is_none(self):
        from throtl.version import parse_tag

        self.assertIsNone(parse_tag({}))
        self.assertIsNone(parse_tag({"tag_name": ""}))
        self.assertIsNone(parse_tag({"tag_name": None}))
        self.assertIsNone(parse_tag(None))


class IsNewerTest(unittest.TestCase):
    def test_compares_numbers_not_strings(self):
        from throtl.version import is_newer

        self.assertTrue(is_newer("0.8.0", "0.9.0"))
        self.assertTrue(is_newer("0.8.0", "0.10.0"))
        self.assertTrue(is_newer("0.9.0", "0.10.0"))
        self.assertFalse(is_newer("0.10.0", "0.9.0"))

    def test_equal_or_older_is_not_newer(self):
        from throtl.version import is_newer

        self.assertFalse(is_newer("0.8.0", "0.8.0"))
        self.assertFalse(is_newer("0.9.0", "0.8.0"))

    def test_tolerates_prefix_and_different_lengths(self):
        from throtl.version import is_newer

        self.assertTrue(is_newer("0.8", "0.8.1"))
        self.assertTrue(is_newer("v0.8.0", "v0.9.0"))
        self.assertFalse(is_newer("0.8.0.1", "0.8.0"))

    def test_unparseable_version_never_nags(self):
        from throtl.version import is_newer

        self.assertFalse(is_newer("0.8.0", "nightly"))
        self.assertFalse(is_newer("0.8.0", ""))
        self.assertFalse(is_newer("0.8.0", None))


class FetchLatestTest(unittest.TestCase):
    def test_reads_the_tag_from_the_api(self):
        from throtl.version import fetch_latest

        seen = {}

        def opener(request, timeout=None):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return _Response(b'{"tag_name": "v0.9.0"}')

        self.assertEqual(fetch_latest(opener=opener), "0.9.0")
        self.assertIn("api.github.com", seen["url"])
        self.assertIsNotNone(seen["timeout"])

    def test_http_error_is_none(self):
        from throtl.version import fetch_latest

        def opener(request, timeout=None):
            return _Response(b"nope", status=404)

        self.assertIsNone(fetch_latest(opener=opener))

    def test_network_failure_is_none_not_an_exception(self):
        from throtl.version import fetch_latest

        def opener(request, timeout=None):
            raise OSError("offline")

        self.assertIsNone(fetch_latest(opener=opener))

    def test_garbage_body_is_none(self):
        from throtl.version import fetch_latest

        def opener(request, timeout=None):
            return _Response(b"<html>not json</html>")

        self.assertIsNone(fetch_latest(opener=opener))

    def test_sends_no_user_data(self):
        """Der Check darf nichts ueber den Nutzer mitschicken."""
        from throtl.version import fetch_latest

        seen = {}

        def opener(request, timeout=None):
            seen["headers"] = {k.lower(): v
                               for k, v in request.header_items()}
            return _Response(b'{"tag_name": "v0.9.0"}')

        fetch_latest(opener=opener)
        for header in ("authorization", "cookie"):
            self.assertNotIn(header, seen["headers"])


if __name__ == "__main__":
    unittest.main()
