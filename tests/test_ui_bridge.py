import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kvn import chain, master
from kvn.settings import DEFAULTS

_UI_APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "app.py")
_SPEC = importlib.util.spec_from_file_location("kvn_ui_app", _UI_APP_PATH)
ui_app = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ui_app)


class UiBridgeOfflineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.sub_file = os.path.join(self.tmp, "sub_url")
        self.chain_file = os.path.join(self.tmp, "chain_url")
        self.patches = [
            mock.patch.object(ui_app, "CONF_DIR", self.tmp),
            mock.patch.object(ui_app, "STATE_DIR", self.tmp),
            mock.patch.object(ui_app, "SUB_URL_FILE", self.sub_file),
            mock.patch.object(ui_app, "CHAIN_URL_FILE", self.chain_file),
            mock.patch.object(ui_app, "LOG_FILE", os.path.join(self.tmp, "kvn.log")),
            mock.patch.object(ui_app, "daemon_pid", return_value=None),
            mock.patch.object(ui_app, "load_settings", return_value=dict(DEFAULTS)),
            mock.patch.object(ui_app, "load_sub_meta", return_value=None),
            mock.patch.object(master, "MASTER_OFF_FILE", os.path.join(self.tmp, "master_off")),
            mock.patch.object(chain, "CHAIN_URL_FILE", self.chain_file),
            mock.patch.object(chain, "CHAIN_OFF_FILE", os.path.join(self.tmp, "chain_off")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.bridge = ui_app.Bridge()

    def test_offline_status_and_config(self):
        st = self.bridge.get_status()
        self.assertFalse(st["running"])
        self.assertEqual(st["state"], "stopped")
        self.assertFalse(st["has_sub_url"])

        cfg = self.bridge.get_config()
        self.assertEqual(cfg["effective"]["port"], DEFAULTS["port"])
        self.assertFalse(cfg["has_sub_url"])

    def test_offline_save_config_and_toggles(self):
        bad = self.bridge.save_config({"overrides": {"bogus_key": 123}})
        self.assertFalse(bad["ok"])

        good = self.bridge.save_config({
            "overrides": {"port": 10811, "chain_port": 10812},
            "sub_url": "https://example.com/sub",
        })
        self.assertTrue(good["ok"])
        with open(os.path.join(self.tmp, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["port"], 10811)
        with open(self.sub_file, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "https://example.com/sub")

        m = self.bridge.set_master(False)
        self.assertTrue(m["ok"])
        self.assertFalse(m["enabled"])

        c = self.bridge.set_chain(False)
        self.assertTrue(c["ok"])
        self.assertFalse(c["enabled"])

    def test_offline_start_requires_sub_url(self):
        res = self.bridge.start_service()
        self.assertFalse(res["ok"])
        self.assertIn("subscription URL", res["error"])


if __name__ == "__main__":
    unittest.main()
