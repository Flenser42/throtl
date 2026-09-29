"""socket_access_hint: den 'Session kennt die Gruppe nicht'-Fall erkennen."""

import types
import unittest
from unittest import mock

import throtl


class SocketAccessHintTest(unittest.TestCase):
    @staticmethod
    def _stat(gid=959, mode=0o660):
        return types.SimpleNamespace(st_gid=gid, st_mode=mode)

    def test_none_when_socket_missing(self):
        with mock.patch.object(throtl.os, "stat", side_effect=FileNotFoundError):
            self.assertIsNone(throtl.socket_access_hint("/nonexistent"))

    def test_none_when_accessible(self):
        with mock.patch.object(throtl.os, "stat", return_value=self._stat()), \
             mock.patch.object(throtl.os, "access", return_value=True):
            self.assertIsNone(throtl.socket_access_hint("/run/throtl/daemon.sock"))

    def test_none_for_root(self):
        with mock.patch.object(throtl.os, "geteuid", return_value=0):
            self.assertIsNone(throtl.socket_access_hint("/run/throtl/daemon.sock"))

    def test_hint_when_session_lacks_group(self):
        group = types.SimpleNamespace(gr_name="throtl")
        with mock.patch.object(throtl.os, "stat", return_value=self._stat()), \
             mock.patch.object(throtl.os, "access", return_value=False), \
             mock.patch.object(throtl.grp, "getgrgid", return_value=group), \
             mock.patch.object(throtl.os, "getgroups", return_value=[1000]):
            hint = throtl.socket_access_hint("/run/throtl/daemon.sock")
        self.assertIsNotNone(hint)
        self.assertIn("throtl", hint)
        self.assertIn("newgrp", hint)

    def test_none_when_group_is_present(self):
        group = types.SimpleNamespace(gr_name="throtl")
        with mock.patch.object(throtl.os, "stat", return_value=self._stat()), \
             mock.patch.object(throtl.os, "access", return_value=False), \
             mock.patch.object(throtl.grp, "getgrgid", return_value=group), \
             mock.patch.object(throtl.os, "getgroups", return_value=[959]):
            self.assertIsNone(throtl.socket_access_hint("/run/throtl/daemon.sock"))


class SecureSocketTest(unittest.TestCase):
    """_secure_socket() must fail closed, never world-accessible."""

    def _daemon(self, path="/run/throtl/daemon.sock"):
        from throtl.daemon import Daemon

        obj = Daemon.__new__(Daemon)
        obj.socket_path = path
        return obj

    def test_root_with_group_sets_0660(self):
        daemon = self._daemon()
        group = types.SimpleNamespace(gr_gid=959)
        with mock.patch("throtl.daemon.os.geteuid", return_value=0), \
             mock.patch("grp.getgrnam", return_value=group), \
             mock.patch("throtl.daemon.os.chown") as chown, \
             mock.patch("throtl.daemon.os.chmod") as chmod:
            daemon._secure_socket()
        chown.assert_called_once_with(daemon.socket_path, 0, 959)
        chmod.assert_called_once_with(daemon.socket_path, throtl.SOCKET_MODE)

    def test_root_without_group_stays_private(self):
        daemon = self._daemon()
        with mock.patch("throtl.daemon.os.geteuid", return_value=0), \
             mock.patch("grp.getgrnam", side_effect=KeyError("throtl")), \
             mock.patch("throtl.daemon.os.chown") as chown, \
             mock.patch("throtl.daemon.os.chmod") as chmod:
            daemon._secure_socket()
        chown.assert_not_called()
        chmod.assert_called_once_with(daemon.socket_path, 0o600)

    def test_non_root_stays_private(self):
        daemon = self._daemon()
        with mock.patch("throtl.daemon.os.geteuid", return_value=1000), \
             mock.patch("throtl.daemon.os.chown") as chown, \
             mock.patch("throtl.daemon.os.chmod") as chmod:
            daemon._secure_socket()
        chown.assert_not_called()
        chmod.assert_called_once_with(daemon.socket_path, 0o600)


if __name__ == "__main__":
    unittest.main()
