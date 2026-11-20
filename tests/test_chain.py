import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kvn import chain, cli, singbox
from kvn.settings import DEFAULTS
from kvn.subscription import parse_ss, parse_vless

UUID = "11111111-2222-3333-4444-555555555555"
PBK = "SGVsbG9IZWxsb0hlbGxvSGVsbG9IZWxsb0hlbGxvMDA"
SUB1 = parse_vless(f"vless://{UUID}@nl.example.net:443?security=reality&pbk={PBK}&sid=01&sni=a.com#🇳🇱 NL")
VPS1_URL = f"vless://{UUID}@203.0.113.10:443?type=tcp&encryption=none&security=reality&pbk={PBK}&fp=chrome&sni=yahoo.com&sid=9a&flow=xtls-rprx-vision#vps-nixos"
VPS2_URL = f"vless://{UUID}@203.0.113.11:443?type=raw&encryption=none&security=reality&pbk={PBK}&fp=chrome&sni=yahoo.com&sid=ab&flow=xtls-rprx-vision#vps-backup"
SS_URL = "ss://2022-blake3-aes-128-gcm:4abaSwgjMo0%2BrLX%2BpI4%2BEQ%3D%3D@203.0.113.10:8443#vps-ss"


class LoadChainServersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _write(self, content):
        path = os.path.join(self.dir, "chain_url")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_single_link_with_comments_and_blanks(self):
        path = self._write(f"# personal exit\n\n{VPS1_URL}\n")
        servers, skipped = chain.load_chain_servers(path)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["tag"], "vps-nixos")
        self.assertEqual(servers[0]["flow"], "xtls-rprx-vision")
        self.assertEqual(skipped, {})

    def test_multiple_links(self):
        path = self._write(f"{VPS1_URL}\n{VPS2_URL}\n")
        servers, _ = chain.load_chain_servers(path)
        self.assertEqual([s["tag"] for s in servers], ["vps-nixos", "vps-backup"])

    def test_empty_file_raises(self):
        path = self._write("# only comments\n\n")
        with self.assertRaises(ValueError):
            chain.load_chain_servers(path)

    def test_optional_returns_empty_when_missing_or_invalid(self):
        self.assertEqual(chain.load_optional_servers(os.path.join(self.dir, "nope")), [])
        bad = self._write("# empty\n")
        self.assertEqual(chain.load_optional_servers(bad), [])


class BuildChainConfigTest(unittest.TestCase):
    def setUp(self):
        self.rules = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.rules)
        patcher = mock.patch.object(singbox, "RULES_DIR", self.rules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def outbound(self, cfg, tag):
        return next(o for o in cfg["outbounds"] if o["tag"] == tag)

    def test_shared_port_routes_final_through_out_with_detour(self):
        c1 = parse_vless(VPS1_URL)
        cfg = singbox.build_config(dict(DEFAULTS), [SUB1], "s", chain_servers=[c1], chain_on=True)
        self.assertEqual(len(cfg["inbounds"]), 1)
        self.assertEqual(cfg["route"]["final"], "out")
        out = self.outbound(cfg, "out")
        self.assertEqual(out["outbounds"], ["select", "chain:vps-nixos"])
        self.assertEqual(out["default"], "chain:vps-nixos")
        node = self.outbound(cfg, "chain:vps-nixos")
        self.assertEqual(node["detour"], "select")

    def test_chain_off_defaults_out_to_select(self):
        c1 = parse_vless(VPS1_URL)
        cfg = singbox.build_config(dict(DEFAULTS), [SUB1], "s", chain_servers=[c1], chain_on=False)
        self.assertEqual(self.outbound(cfg, "out")["default"], "select")

    def test_dedicated_chain_port_keeps_main_port_on_select(self):
        c1 = parse_vless(VPS1_URL)
        cfg = singbox.build_config({**DEFAULTS, "chain_port": 10809}, [SUB1], "s", chain_servers=[c1], chain_on=True)
        self.assertEqual([i["listen_port"] for i in cfg["inbounds"]], [10808, 10809])
        self.assertEqual(cfg["route"]["final"], "select")
        self.assertIn({"inbound": ["chain-in"], "outbound": "out"}, cfg["route"]["rules"])

    def test_auto_mode_builds_urltest_of_direct_and_detour(self):
        c1 = parse_vless(VPS1_URL)
        cfg = singbox.build_config({**DEFAULTS, "chain_via": "auto"}, [SUB1], "s", chain_servers=[c1], chain_on=True)
        grp = self.outbound(cfg, "chain")
        self.assertEqual(grp["type"], "urltest")
        self.assertEqual(grp["outbounds"], ["chain:vps-nixos (direct)", "chain:vps-nixos (via sub)"])
        self.assertNotIn("detour", self.outbound(cfg, "chain:vps-nixos (direct)"))
        self.assertEqual(self.outbound(cfg, "chain:vps-nixos (via sub)")["detour"], "select")
        self.assertEqual(self.outbound(cfg, "out")["default"], "chain")

    def test_direct_mode_has_no_detour(self):
        c1 = parse_vless(VPS1_URL)
        cfg = singbox.build_config({**DEFAULTS, "chain_via": "direct"}, [SUB1], "s", chain_servers=[c1], chain_on=True)
        self.assertNotIn("detour", self.outbound(cfg, "chain:vps-nixos"))


@unittest.skipUnless(shutil.which(singbox.SING_BOX), "sing-box not installed")
class SingBoxAcceptsChainConfigTest(unittest.TestCase):
    def test_all_modes_accepted(self):
        c1, c2, ss = parse_vless(VPS1_URL), parse_vless(VPS2_URL), parse_ss(SS_URL)
        cases = [
            ({**DEFAULTS, "bypass_russia": False, "chain_via": "chain", "chain_port": None}, [c1]),
            ({**DEFAULTS, "bypass_russia": False, "chain_via": "chain", "chain_port": 10809}, [ss]),
            ({**DEFAULTS, "bypass_russia": False, "chain_via": "auto", "chain_port": 10809}, [ss]),
            ({**DEFAULTS, "bypass_russia": False, "chain_via": "direct", "chain_port": 10809}, [c1, c2]),
        ]
        for settings, servers in cases:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(singbox.build_config(settings, [SUB1], "s", chain_servers=servers, chain_on=True), f)
            self.addCleanup(os.unlink, f.name)
            ok, err = singbox.check_config(f.name)
            self.assertTrue(ok, err)


class ChainCliTest(unittest.TestCase):
    def test_chain_check_fails_when_file_missing(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with mock.patch.object(cli, "CHAIN_URL_FILE", os.path.join(d, "missing")):
            rc = cli.main(["chain", "check"])
        self.assertEqual(rc, 1)

    def test_chain_toggle_persists_off_file(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        url_file = os.path.join(d, "chain_url")
        off_file = os.path.join(d, "chain_off")
        with open(url_file, "w") as f:
            f.write(VPS1_URL + "\n")
        with mock.patch.object(chain, "CHAIN_URL_FILE", url_file), \
             mock.patch.object(chain, "CHAIN_OFF_FILE", off_file):
            self.assertTrue(chain.enabled())
            chain.set_enabled(False)
            self.assertFalse(chain.enabled())
            chain.set_enabled(True)
            self.assertTrue(chain.enabled())


if __name__ == "__main__":
    unittest.main()
