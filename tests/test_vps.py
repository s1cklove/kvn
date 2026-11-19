import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kvn import cli, singbox, vps
from kvn.settings import DEFAULTS
from kvn.subscription import parse_vless

UUID = "11111111-2222-3333-4444-555555555555"
PBK = "SGVsbG9IZWxsb0hlbGxvSGVsbG9IZWxsb0hlbGxvMDA"
VPS1 = f"vless://{UUID}@203.0.113.10:443?type=tcp&encryption=none&security=reality&pbk={PBK}&fp=chrome&sni=www.microsoft.com&sid=9a&flow=xtls-rprx-vision#vps-nixos"
VPS2 = f"vless://{UUID}@203.0.113.11:443?type=raw&encryption=none&security=reality&pbk={PBK}&fp=chrome&sni=www.microsoft.com&sid=ab&flow=xtls-rprx-vision#vps-backup"


class LoadVpsServersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _write(self, content):
        path = os.path.join(self.dir, "vps_url")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_single_link_with_comments_and_blanks(self):
        path = self._write(f"# my personal VPS\n\n{VPS1}\n")
        servers, skipped = vps.load_vps_servers(path)
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["tag"], "vps-nixos")
        self.assertEqual(servers[0]["server"], "203.0.113.10")
        self.assertEqual(servers[0]["flow"], "xtls-rprx-vision")
        self.assertEqual(skipped, {})

    def test_multiple_links(self):
        path = self._write(f"{VPS1}\n{VPS2}\n")
        servers, skipped = vps.load_vps_servers(path)
        self.assertEqual([s["tag"] for s in servers], ["vps-nixos", "vps-backup"])
        self.assertEqual(skipped, {})

    def test_empty_file_raises(self):
        path = self._write("# only comments\n\n")
        with self.assertRaises(ValueError):
            vps.load_vps_servers(path)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            vps.load_vps_servers(os.path.join(self.dir, "nope"))


class BuildVpsConfigTest(unittest.TestCase):
    def setUp(self):
        self.rules = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.rules)
        patcher = mock.patch.object(singbox, "RULES_DIR", self.rules)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_single_server_routes_directly_without_urltest(self):
        s1 = parse_vless(VPS1)
        cfg = singbox.build_vps_config(dict(DEFAULTS), [s1])
        self.assertEqual(cfg["inbounds"][0]["listen"], DEFAULTS["vps_listen"])
        self.assertEqual(cfg["inbounds"][0]["listen_port"], DEFAULTS["vps_port"])
        self.assertEqual([o["tag"] for o in cfg["outbounds"]], ["vps-nixos", "direct"])
        self.assertEqual(cfg["route"]["final"], "vps-nixos")
        self.assertNotIn("experimental", cfg)

    def test_multiple_servers_use_urltest(self):
        s1, s2 = parse_vless(VPS1), parse_vless(VPS2)
        cfg = singbox.build_vps_config(dict(DEFAULTS), [s1, s2])
        self.assertEqual(cfg["outbounds"][0]["type"], "urltest")
        self.assertEqual(cfg["outbounds"][0]["outbounds"], ["vps-nixos", "vps-backup"])
        self.assertEqual(cfg["route"]["final"], "auto")

    def test_vps_bypass_russia_can_be_disabled(self):
        s1 = parse_vless(VPS1)
        with open(os.path.join(self.rules, "geoip-ru.srs"), "wb") as f:
            f.write(b"SRS")
        cfg_on = singbox.build_vps_config({**DEFAULTS, "vps_bypass_russia": True}, [s1])
        cfg_off = singbox.build_vps_config({**DEFAULTS, "vps_bypass_russia": False}, [s1])
        self.assertEqual([r["tag"] for r in cfg_on["route"]["rule_set"]], ["geoip-ru"])
        self.assertEqual(cfg_off["route"]["rule_set"], [])
        self.assertFalse(any("domain_suffix" in r for r in cfg_off["route"]["rules"]))


@unittest.skipUnless(shutil.which(singbox.SING_BOX), "sing-box not installed")
class SingBoxAcceptsVpsConfigTest(unittest.TestCase):
    def test_single_and_multi_check(self):
        for servers in ([parse_vless(VPS1)], [parse_vless(VPS1), parse_vless(VPS2)]):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(singbox.build_vps_config({**DEFAULTS, "vps_bypass_russia": False}, servers), f)
            self.addCleanup(os.unlink, f.name)
            ok, err = singbox.check_config(f.name)
            self.assertTrue(ok, err)


class VpsCliTest(unittest.TestCase):
    def test_bare_vps_dispatches_to_status(self):
        with mock.patch.object(cli, "cmd_vps_status", return_value=0) as m:
            rc = cli.main(["vps"])
        self.assertEqual(rc, 0)
        m.assert_called_once()

    def test_vps_check_fails_when_file_missing(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        with mock.patch.object(cli, "VPS_URL_FILE", os.path.join(d, "missing")):
            rc = cli.main(["vps", "check"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
