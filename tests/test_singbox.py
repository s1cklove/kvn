import os
import shutil
import tempfile
import unittest
from unittest import mock

from kvn import singbox
from kvn.settings import DEFAULTS
from kvn.subscription import parse_vless

UUID = "11111111-2222-3333-4444-555555555555"
NL = parse_vless(f"vless://{UUID}@nl.example.net:443?security=reality&pbk=SGVsbG9IZWxsb0hlbGxvSGVsbG9IZWxsb0hlbGxvMDA&sid=01&sni=a.com#🇳🇱 NL")
RU = parse_vless(f"vless://{UUID}@ru.example.net:443?security=reality&pbk=SGVsbG9IZWxsb0hlbGxvSGVsbG9IZWxsb0hlbGxvMDA&sid=01&sni=a.com#🇷🇺 Россия")


class BuildConfigTest(unittest.TestCase):
    def setUp(self):
        self.rules = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.rules)
        patcher = mock.patch.object(singbox, "RULES_DIR", self.rules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def outbound(self, cfg, tag):
        return next(o for o in cfg["outbounds"] if o["tag"] == tag)

    def test_excluded_servers_are_left_out(self):
        cfg = singbox.build_config(dict(DEFAULTS), [NL, RU], "s")
        auto = self.outbound(cfg, "auto")
        self.assertEqual(auto["type"], "urltest")
        self.assertEqual(auto["outbounds"], ["🇳🇱 NL"])
        self.assertEqual(cfg["inbounds"][0]["listen_port"], DEFAULTS["port"])

    def test_routing_goes_through_the_selector(self):
        cfg = singbox.build_config(dict(DEFAULTS), [NL, RU], "s")
        select = self.outbound(cfg, "select")
        self.assertEqual(select["type"], "selector")
        self.assertEqual(select["outbounds"], ["auto", "🇳🇱 NL"])  # only selectable exits, same as auto
        self.assertEqual(select["default"], "auto")
        self.assertEqual(cfg["route"]["final"], "select")

    def test_everything_excluded_falls_back_to_all(self):
        cfg = singbox.build_config(dict(DEFAULTS), [RU], "s")
        self.assertEqual(self.outbound(cfg, "auto")["outbounds"], ["🇷🇺 Россия"])

    def test_only_downloaded_rulesets_are_referenced(self):
        open(os.path.join(self.rules, "geoip-ru.srs"), "wb").write(b"SRS")
        cfg = singbox.build_config(dict(DEFAULTS), [NL], "s")
        self.assertEqual([r["tag"] for r in cfg["route"]["rule_set"]], ["geoip-ru"])

    def test_bypass_off(self):
        cfg = singbox.build_config({**DEFAULTS, "bypass_russia": False}, [NL], "s")
        self.assertEqual(cfg["route"]["rule_set"], [])
        self.assertFalse(any("domain_suffix" in r for r in cfg["route"]["rules"]))


@unittest.skipUnless(shutil.which(singbox.SING_BOX), "sing-box not installed")
class SingBoxAcceptsConfigTest(unittest.TestCase):
    def test_check(self):
        import json
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(singbox.build_config({**DEFAULTS, "bypass_russia": False}, [NL], "s"), f)
        self.addCleanup(os.unlink, f.name)
        ok, err = singbox.check_config(f.name)
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
