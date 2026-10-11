import json
import time
import urllib.parse
import urllib.request


class Clash:
    def __init__(self, addr, secret):
        self.base = f"http://{addr}"
        self.secret = secret

    def get(self, path, timeout=5):
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.secret}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            return json.load(r)

    def current(self):
        return self.get("/proxies/auto")["now"]

    def last_delay(self, tag):
        hist = self.get("/proxies/" + urllib.parse.quote(tag, safe="")).get("history") or []
        return hist[-1]["delay"] if hist else 0

    def test_all(self, url, timeout_ms=120000, deadline=300):
        """Re-tests every server in the urltest group; urltest re-picks the best afterwards.

        While urltest is already mid-check (always right after start) the endpoint returns {}
        instantly, so an empty answer that came back fast means "busy, ask again".
        timeout_ms bounds the whole group run (dead servers each hang until sing-box's own
        dial timeout), not a single server.
        """
        q = urllib.parse.urlencode({"url": url, "timeout": timeout_ms})
        give_up = time.monotonic() + deadline
        while True:
            started = time.monotonic()
            res = self.get(f"/group/auto/delay?{q}", timeout=timeout_ms / 1000 + 60)
            alive = {k: v for k, v in res.items() if v and v > 0}
            if alive or time.monotonic() - started > 1 or time.monotonic() > give_up:
                return alive
            time.sleep(2)

    def delays(self):
        group = self.get("/proxies/auto")
        out = {}
        for tag in group.get("all", []):
            hist = self.get("/proxies/" + urllib.parse.quote(tag, safe="")).get("history") or []
            out[tag] = hist[-1]["delay"] if hist else 0
        return group["now"], out


def top_list(delays, n=3):
    best = sorted(delays.items(), key=lambda kv: kv[1])[:n]
    return ", ".join(f"{k} {v} мс" for k, v in best)
