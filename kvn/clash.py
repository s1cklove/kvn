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

    def put(self, path, body, timeout=5):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), method="PUT",
            headers={"Authorization": f"Bearer {self.secret}", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(req, timeout=timeout).close()  # 204, no body

    def selector(self):
        """The manual routing selector: now is 'auto' or a pinned server; all lists the choices."""
        return self.get("/proxies/select")

    def select(self, name):
        """Point manual routing at 'auto' or a specific server tag."""
        self.put("/proxies/select", {"name": name})

    def auto_selected(self):
        """What automatic routing uses: 'fastest' (urltest) or the master server tag."""
        return self.get("/proxies/auto")["now"]

    def select_auto(self, name):
        """Point automatic routing at 'fastest' (urltest) or the master server tag."""
        self.put("/proxies/auto", {"name": name})

    def out_selector(self):
        """Top-level chain selector ('out'): now is 'select' or the chain target."""
        return self.get("/proxies/out")

    def select_out(self, name):
        """Point 'out' at 'select' (subscription only) or the chain target."""
        self.put("/proxies/out", {"name": name})

    def chain_current(self):
        """Active chain outbound tag when chain is enabled, or None if bypassed/unconfigured."""
        now = self.out_selector().get("now")
        if not now or now == "select":
            return None
        if now == "chain":
            return self.get("/proxies/chain").get("now")
        return now

    def current(self):
        """The server traffic actually goes through."""
        sel = self.selector().get("now")
        if sel and sel != "auto":
            return sel
        auto = self.auto_selected()
        return self.get("/proxies/fastest")["now"] if auto == "fastest" else auto

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
            res = self.get(f"/group/fastest/delay?{q}", timeout=timeout_ms / 1000 + 60)
            alive = {k: v for k, v in res.items() if v and v > 0}
            if alive or time.monotonic() - started > 1 or time.monotonic() > give_up:
                return alive
            time.sleep(2)

    def delays(self):
        """(urltest pick, {tag: last delay or 0 if dead}) for every server in the group."""
        proxies = self.get("/proxies")["proxies"]
        group = proxies["fastest"]
        out = {}
        for tag in group.get("all", []):
            hist = proxies.get(tag, {}).get("history") or []
            out[tag] = hist[-1]["delay"] if hist else 0
        return group["now"], out


def top_list(delays, n=3):
    best = sorted(delays.items(), key=lambda kv: kv[1])[:n]
    return ", ".join(f"{k} ({v} ms)" for k, v in best)
