import os
import shutil
import tempfile
import unittest
from unittest import mock

from kvn import master
from kvn.settings import DEFAULTS

S = {**DEFAULTS, "master": "NL", "master_max_ratio": 2, "master_max_lag": 150}


class MasterTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        patcher = mock.patch.object(master, "MASTER_OFF_FILE", os.path.join(d, "master_off"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_within_ratio(self):
        # 2× the leader but 250 ms behind: the ratio alone is enough
        self.assertEqual(master.choose(S, {"NL a": 500, "KZ": 250}), "NL a")

    def test_within_lag(self):
        # 5× the leader but only 120 ms behind: the lag alone is enough
        self.assertEqual(master.choose(S, {"NL a": 150, "KZ": 30}), "NL a")

    def test_too_slow(self):
        self.assertEqual(master.choose(S, {"NL a": 501, "KZ": 250}), "fastest")

    def test_dead_or_missing(self):
        self.assertEqual(master.choose(S, {"NL a": 0, "KZ": 250}), "fastest")
        self.assertEqual(master.choose(S, {"KZ": 250}), "fastest")
        self.assertEqual(master.choose(S, {}), "fastest")

    def test_fastest_match_wins(self):
        self.assertEqual(master.choose(S, {"NL a": 300, "NL b": 200, "KZ": 250}), "NL b")

    def test_off(self):
        master.set_enabled(False)
        self.assertEqual(master.choose(S, {"NL a": 100, "KZ": 250}), "fastest")
        master.set_enabled(True)
        master.set_enabled(True)
        self.assertEqual(master.choose(S, {"NL a": 100, "KZ": 250}), "NL a")

    def test_not_configured(self):
        self.assertEqual(master.choose({**S, "master": ""}, {"NL a": 100}), "fastest")


if __name__ == "__main__":
    unittest.main()
