import json
import logging
import os
import shutil
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

from kvn import api, chain, master
from kvn.settings import DEFAULTS


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeClash:
    def __init__(self, secret="test-secret"):
        self.secret = secret
        self.sel = "auto"
        self.auto = "fastest"
        self.out = "select"
        self.members = ["auto", "🇳🇱 NL", "🇩🇪 DE"]
        self.server_delays = {"🇳🇱 NL": 120, "🇩🇪 DE": 150}

    def selector(self):
        return {"now": self.sel, "all": self.members}

    def select(self, name):
        self.sel = name

    def auto_selected(self):
        return self.auto

    def select_auto(self, name):
        self.auto = name

    def out_selector(self):
        return {"now": self.out, "all": ["select", "chain:vps"]}

    def select_out(self, name):
        self.out = name

    def chain_current(self):
        return None if self.out == "select" else self.out

    def current(self):
        if self.sel != "auto":
            return self.sel
        return "🇳🇱 NL" if self.auto == "fastest" else self.auto

    def last_delay(self, tag):
        return self.server_delays.get(tag, 0)

    def delays(self):
        return "🇳🇱 NL", dict(self.server_delays)

    def test_all(self, url):
        return dict(self.server_delays)


class FakeWatchdog:
    def __init__(self, port):
        self.s = {**DEFAULTS, "api_listen": "127.0.0.1", "api_port": port, "master": "NL"}
        self.proxy = "http://127.0.0.1:10808"
        self.clash = FakeClash()
        self.servers = []
        self.state = "up"
        self.reloaded = False
        self.stopped = False

    def pinned(self):
        return None if self.clash.sel == "auto" else self.clash.sel

    def steer(self):
        pass

    def group_members(self):
        return list(self.clash.server_delays)

    def on_hup(self):
        self.reloaded = True

    def on_stop(self):
        self.stopped = True


class ControlApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.port = _free_port()
        self.wd = FakeWatchdog(self.port)
        self.ring = api.RingLogHandler(capacity=20)
        self.ring.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        rec = logging.LogRecord("kvn", logging.INFO, "", 0, "hello api", (), None)
        self.ring.emit(rec)

        self.patches = [
            mock.patch.object(api, "CONF_DIR", self.tmp),
            mock.patch.object(api, "STATE_DIR", self.tmp),
            mock.patch.object(api, "SUB_URL_FILE", os.path.join(self.tmp, "sub_url")),
            mock.patch.object(api, "CHAIN_URL_FILE", os.path.join(self.tmp, "chain_url")),
            mock.patch.object(api, "probe", return_value=True),
            mock.patch.object(api, "load_settings", return_value=dict(self.wd.s)),
            mock.patch.object(master, "MASTER_OFF_FILE", os.path.join(self.tmp, "master_off")),
            mock.patch.object(chain, "CHAIN_URL_FILE", os.path.join(self.tmp, "chain_url")),
            mock.patch.object(chain, "CHAIN_OFF_FILE", os.path.join(self.tmp, "chain_off")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

        self.srv = api.start_api_server(self.wd, self.ring)
        self.assertIsNotNone(self.srv)
        self.addCleanup(self._stop_srv)
        self.addr = f"127.0.0.1:{self.port}"

    def _stop_srv(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_requires_bearer_token(self):
        req = urllib.request.Request(f"http://{self.addr}/health")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 401)
        ctx.exception.close()

    def test_blocks_untrusted_origin(self):
        req = urllib.request.Request(
            f"http://{self.addr}/health",
            headers={"Authorization": "Bearer test-secret", "Origin": "https://evil.example"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(ctx.exception.code, 403)
        ctx.exception.close()

    def test_status_servers_and_logs(self):
        st = api.api_request(self.addr, "test-secret", "GET", "/status")
        self.assertTrue(st["running"])
        self.assertTrue(st["proxy_ok"])
        self.assertEqual(st["server"], "🇳🇱 NL")

        srvs = api.api_request(self.addr, "test-secret", "GET", "/servers")
        self.assertEqual(len(srvs["servers"]), 2)
        self.assertTrue(srvs["servers"][0]["active"])

        logs = api.api_request(self.addr, "test-secret", "GET", "/logs?lines=10")
        self.assertEqual(logs["lines"], ["INFO hello api"])

    def test_use_master_and_stop(self):
        res = api.api_request(self.addr, "test-secret", "POST", "/use", {"server": "de"})
        self.assertEqual(res["target"], "🇩🇪 DE")
        self.assertEqual(self.wd.clash.sel, "🇩🇪 DE")

        m = api.api_request(self.addr, "test-secret", "POST", "/master", {"enabled": False})
        self.assertFalse(m["enabled"])

        api.api_request(self.addr, "test-secret", "POST", "/reload")
        self.assertTrue(self.wd.reloaded)

        api.api_request(self.addr, "test-secret", "POST", "/stop")
        self.assertTrue(self.wd.stopped)

    def test_put_config_rejects_unknown_keys_and_saves_valid_ones(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            api.api_request(self.addr, "test-secret", "PUT", "/config", {"overrides": {"nope_key": 1}})
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

        api.api_request(self.addr, "test-secret", "PUT", "/config", {
            "overrides": {"port": 10810, "bypass_russia": False},
            "sub_url": "https://example.com/sub",
        })
        with open(os.path.join(self.tmp, "config.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["port"], 10810)
        with open(os.path.join(self.tmp, "sub_url"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "https://example.com/sub")


if __name__ == "__main__":
    unittest.main()
