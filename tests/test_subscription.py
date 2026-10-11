import base64
import unittest

from kvn.subscription import (
    Unsupported, decode_subscription, parse_subscription, parse_vless, servers_digest, subscription_expiry,
)

UUID = "11111111-2222-3333-4444-555555555555"
REALITY = f"vless://{UUID}@nl.example.net:443?security=reality&pbk=PUBKEY&sid=ab12&sni=www.example.com&fp=chrome&flow=xtls-rprx-vision&type=tcp#%F0%9F%87%B3%F0%9F%87%B1%20Netherlands"
WS = f"vless://{UUID}@de.example.net:443?security=tls&sni=cdn.example.com&type=ws&path=%2Fws%3Fed%3D2048&host=cdn.example.com#Germany"


class DecodeTest(unittest.TestCase):
    def test_plain_links(self):
        self.assertEqual(decode_subscription(f"{REALITY}\n\n{WS}\n".encode()), [REALITY, WS])

    def test_base64_urlsafe_without_padding(self):
        blob = base64.urlsafe_b64encode(f"{REALITY}\n{WS}".encode()).rstrip(b"=")
        self.assertEqual(decode_subscription(blob), [REALITY, WS])


class VlessTest(unittest.TestCase):
    def test_reality(self):
        ob = parse_vless(REALITY)
        self.assertEqual(ob["tag"], "🇳🇱 Netherlands")
        self.assertEqual((ob["server"], ob["server_port"], ob["uuid"], ob["flow"]), ("nl.example.net", 443, UUID, "xtls-rprx-vision"))
        self.assertEqual(ob["tls"]["reality"], {"enabled": True, "public_key": "PUBKEY", "short_id": "ab12"})
        self.assertEqual(ob["tls"]["utls"]["fingerprint"], "chrome")
        self.assertNotIn("transport", ob)

    def test_ws_early_data_moves_out_of_path(self):
        t = parse_vless(WS)["transport"]
        self.assertEqual(t["path"], "/ws")
        self.assertEqual(t["max_early_data"], 2048)
        self.assertEqual(t["headers"], {"Host": "cdn.example.com"})

    def test_reality_without_key_is_rejected(self):
        with self.assertRaises(Unsupported):
            parse_vless(f"vless://{UUID}@h:443?security=reality#x")


class ParseSubscriptionTest(unittest.TestCase):
    def test_skips_unsupported_and_dedupes_tags(self):
        body = "\n".join([WS, WS, "vmess://abc", f"vless://{UUID}@h:1?security=none#auto"]).encode()
        servers, skipped = parse_subscription(body)
        self.assertEqual([s["tag"] for s in servers], ["Germany", "Germany #2", "srv auto"])
        self.assertEqual(skipped, {"scheme vmess": 1})

    def test_rotation_only_counts_in_full_digest(self):
        a, _ = parse_subscription(REALITY.encode())
        b, _ = parse_subscription(REALITY.replace("sid=ab12", "sid=cd34").encode())
        self.assertNotEqual(servers_digest(a), servers_digest(b))
        self.assertEqual(servers_digest(a, full=False), servers_digest(b, full=False))


class ExpiryTest(unittest.TestCase):
    def test_header(self):
        self.assertEqual(subscription_expiry({"subscription-userinfo": "upload=0; download=1; expire=1767225600"}), 1767225600)
        self.assertIsNone(subscription_expiry({"subscription-userinfo": "expire=0"}))
        self.assertIsNone(subscription_expiry(None))


if __name__ == "__main__":
    unittest.main()
