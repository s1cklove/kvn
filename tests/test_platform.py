import os
import unittest

from kvn import platform


class PathsTest(unittest.TestCase):
    def test_linux_xdg_defaults_and_overrides(self):
        c, s, r = platform.paths("linux", env={}, home="/home/u")
        self.assertEqual(c, "/home/u/.config/kvn")
        self.assertEqual(s, "/home/u/.local/state/kvn")
        self.assertEqual(r, s)

        c2, s2, r2 = platform.paths(
            "linux",
            env={"XDG_CONFIG_HOME": "/c", "XDG_STATE_HOME": "/s", "XDG_RUNTIME_DIR": "/run/u"},
            home="/home/u",
        )
        self.assertEqual((c2, s2, r2), ("/c/kvn", "/s/kvn", "/run/u"))

    def test_macos_paths_are_distinct(self):
        c, s, r = platform.paths("darwin", env={}, home="/Users/u")
        self.assertEqual(c, "/Users/u/Library/Application Support/kvn")
        self.assertEqual(s, "/Users/u/Library/Application Support/kvn/state")
        self.assertEqual(r, s)
        self.assertNotEqual(c, s)

    def test_windows_paths_are_distinct(self):
        c, s, r = platform.paths(
            "win32",
            env={"APPDATA": r"C:\Users\u\AppData\Roaming", "LOCALAPPDATA": r"C:\Users\u\AppData\Local"},
            home=r"C:\Users\u",
        )
        self.assertTrue(c.endswith("Roaming/kvn") or c.endswith(r"Roaming\kvn"))
        self.assertTrue(s.endswith("Local/kvn") or s.endswith(r"Local\kvn"))
        self.assertEqual(r, s)
        self.assertNotEqual(c, s)

    def test_is_pid_alive_current_process(self):
        self.assertTrue(platform.is_pid_alive(os.getpid()))
        self.assertFalse(platform.is_pid_alive(-1))
        self.assertFalse(platform.is_pid_alive(0))


if __name__ == "__main__":
    unittest.main()
