"""Localhost JSON control API (`127.0.0.1:9096`) for CLI IPC and isolated UIs.

Uses Python stdlib `http.server` only. Authenticated with the same Bearer token
as the sing-box Clash API (`STATE_DIR/clash_secret`) and restricted to loopback
origins so untrusted web pages cannot talk to it.
"""

from collections import deque
import hmac
import http.server
import json
import logging
import os
import threading
import urllib.parse
import urllib.request

from . import __version__, chain, master
from .net import probe
from .platform import is_config_externally_managed
from .settings import (
    CHAIN_URL_FILE, CONF_DIR, DEFAULTS, STATE_DIR, SUB_URL_FILE,
    load_settings, load_sub_meta, save_servers, write_atomic,
)
from .singbox import build_config, check_config
from .subscription import fetch_subscription, subscription_expiry

log = logging.getLogger("kvn")

ALLOWED_ORIGINS = (
    "http://127.0.0.1",
    "http://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
)


class RingLogHandler(logging.Handler):
    """Keeps the last N formatted log lines in memory for GET /logs."""

    def __init__(self, capacity=500):
        super().__init__()
        self.lines = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record):
        line = self.format(record)
        with self._lock:
            self.lines.append(line)

    def tail(self, n=100):
        with self._lock:
            if n <= 0:
                return []
            return list(self.lines)[-n:]


def resolve_server(name, members):
    """Maps a user-typed server name onto a selector member (exact or unique substring)."""
    if name in members:
        return name
    hits = [m for m in members if name.lower() in m.lower()]
    if len(hits) == 1:
        return hits[0]
    return hits or None


def api_request(addr, secret, method, path, body=None, timeout=15):
    """Thin client helper used by CLI commands (reload, stop, etc.)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {secret}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://{addr}{path}", data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _origin_allowed(origin):
    if not origin:
        return True
    return any(origin == p or origin.startswith(p + ":") for p in ALLOWED_ORIGINS)


def _status_payload(watchdog):
    s = watchdog.s
    proxy = f"http://{s['listen']}:{s['port']}"
    works = probe(s["probe_urls"], proxy)
    mode = "auto"
    server = None
    delay = 0
    chain_info = {
        "configured": chain.configured(),
        "enabled": chain.enabled(),
        "via": s["chain_via"],
        "port": s.get("chain_port"),
        "active": None,
        "works": None,
        "latency_ms": None,
    }
    try:
        sel = watchdog.clash.selector().get("now")
        if sel and sel != "auto":
            mode = "pinned"
            server = sel
            delay = watchdog.clash.last_delay(sel)
        else:
            auto_sel = watchdog.clash.auto_selected()
            server = watchdog.clash.current()
            delay = watchdog.clash.last_delay(server) if server else 0
            mode = "master" if auto_sel != "fastest" else "auto"
        if chain.configured():
            try:
                c_now = watchdog.clash.chain_current()
            except OSError:
                c_now = None
            chain_info["active"] = chain.display_tag(c_now) if c_now else None
            if c_now:
                ms = chain.probe_latency(s["probe_urls"], chain.chain_proxy_url(s))
                chain_info["works"] = ms is not None
                chain_info["latency_ms"] = ms
    except OSError:
        pass

    return {
        "version": __version__,
        "running": True,
        "pid": os.getpid(),
        "state": watchdog.state,
        "listen": s["listen"],
        "port": s["port"],
        "proxy": f"{s['listen']}:{s['port']}",
        "proxy_ok": works,
        "server": server,
        "delay_ms": delay,
        "mode": mode,
        "master": {
            "configured": bool(s["master"]),
            "pattern": s["master"],
            "enabled": master.enabled(s),
            "max_ratio": s["master_max_ratio"],
            "max_lag": s["master_max_lag"],
        },
        "chain": chain_info,
        "subscription": load_sub_meta(),
        "managed_externally": is_config_externally_managed(os.path.join(CONF_DIR, "config.json")),
    }


def _servers_payload(watchdog):
    _, delays = watchdog.clash.delays()
    sel = watchdog.clash.selector().get("now")
    auto_sel = watchdog.clash.auto_selected()
    routed = watchdog.clash.current()
    mode = "pinned" if (sel and sel != "auto") else ("master" if auto_sel != "fastest" else "auto")
    items = [
        {"tag": tag, "delay_ms": d, "alive": d > 0, "active": tag == routed}
        for tag, d in sorted(delays.items(), key=lambda kv: (kv[1] <= 0, kv[1]))
    ]
    return {"current": routed, "mode": mode, "servers": items}


def _config_payload(watchdog):
    conf_path = os.path.join(CONF_DIR, "config.json")
    overrides = {}
    if os.path.exists(conf_path):
        try:
            with open(conf_path, encoding="utf-8") as f:
                overrides = json.load(f)
        except (OSError, ValueError):
            overrides = {}
    c_servers = chain.load_optional_servers()
    return {
        "effective": dict(watchdog.s),
        "overrides": overrides,
        "defaults": dict(DEFAULTS),
        "has_sub_url": os.path.exists(SUB_URL_FILE),
        "has_chain_url": os.path.exists(CHAIN_URL_FILE),
        "chain_servers": [
            {"tag": s["tag"], "type": s["type"], "server": s["server"], "server_port": s["server_port"]}
            for s in c_servers
        ],
        "managed_externally": is_config_externally_managed(conf_path),
    }


def start_api_server(watchdog, ring_handler):
    """Starts the control HTTP server in a daemon thread. Returns server or None if disabled."""
    port = watchdog.s.get("api_port")
    if not port:
        return None
    listen = watchdog.s.get("api_listen") or watchdog.s["listen"]
    secret = watchdog.clash.secret

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # keep daemon logs quiet

        def _send(self, code, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            origin = self.headers.get("Origin", "")
            if origin and _origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def _auth(self):
            origin = self.headers.get("Origin", "")
            if not _origin_allowed(origin):
                self._send(403, {"error": "forbidden origin"})
                return False
            auth = self.headers.get("Authorization", "")
            expected = f"Bearer {secret}"
            if not hmac.compare_digest(auth, expected):
                self._send(401, {"error": "unauthorized"})
                return False
            return True

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 1024 * 1024:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8")) if raw else {}

        def do_OPTIONS(self):
            origin = self.headers.get("Origin", "")
            if not _origin_allowed(origin):
                self._send(403, {"error": "forbidden origin"})
                return
            self._send(200, {"ok": True})

        def do_GET(self):
            if not self._auth():
                return
            parsed = urllib.parse.urlsplit(self.path)
            route = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                if route == "/health":
                    self._send(200, {"ok": True, "version": __version__, "pid": os.getpid()})
                elif route == "/status":
                    self._send(200, _status_payload(watchdog))
                elif route == "/servers":
                    self._send(200, _servers_payload(watchdog))
                elif route == "/config":
                    self._send(200, _config_payload(watchdog))
                elif route == "/logs":
                    n = int((qs.get("lines") or ["100"])[0])
                    self._send(200, {"lines": ring_handler.tail(max(1, min(n, 500)))})
                else:
                    self._send(404, {"error": "not found"})
            except OSError as e:
                self._send(502, {"error": str(e)})
            except ValueError as e:
                self._send(400, {"error": str(e)})

        def do_POST(self):
            if not self._auth():
                return
            route = urllib.parse.urlsplit(self.path).path
            try:
                body = self._read_json()
                if route == "/reload":
                    watchdog.s = load_settings()
                    watchdog.on_hup()
                    self._send(200, {"ok": True})
                elif route == "/stop":
                    watchdog.on_stop()
                    self._send(200, {"ok": True})
                elif route == "/connect":
                    alive = watchdog.clash.test_all(watchdog.s["test_url"])
                    total = len(watchdog.group_members())
                    if not watchdog.pinned():
                        watchdog.steer()
                    now = watchdog.clash.current()
                    delay = watchdog.clash.last_delay(now) if now else 0
                    self._send(200, {"ok": bool(alive), "alive": len(alive), "total": total, "current": now, "delay_ms": delay})
                elif route == "/use":
                    name = str(body.get("server", "")).strip()
                    members = watchdog.clash.selector().get("all", [])
                    target = resolve_server(name, members)
                    if target is None:
                        self._send(404, {"error": f"no server matches {name!r}"})
                        return
                    if isinstance(target, list):
                        self._send(409, {"error": "ambiguous server name", "matches": target})
                        return
                    watchdog.clash.select(target)
                    if target == "auto":
                        watchdog.steer()
                    self._send(200, {"ok": True, "target": target})
                elif route == "/master":
                    on = bool(body.get("enabled", True))
                    master.set_enabled(on)
                    if not watchdog.pinned():
                        watchdog.steer()
                    self._send(200, {"ok": True, "enabled": master.enabled(watchdog.s)})
                elif route == "/chain":
                    if not chain.configured():
                        self._send(400, {"error": "no chain_url configured"})
                        return
                    on = bool(body.get("enabled", True))
                    chain.set_enabled(on)
                    try:
                        chain.steer(watchdog.clash)
                    except OSError:
                        pass
                    self._send(200, {"ok": True, "enabled": chain.enabled()})
                elif route == "/fetch":
                    servers, skipped, headers, via = fetch_subscription(watchdog.s, [None, watchdog.proxy])
                    exp = subscription_expiry(headers)
                    tmp = os.path.join(STATE_DIR, "config.api-check.json")
                    cfg = build_config(watchdog.s, servers, "check",
                                       chain_servers=chain.load_optional_servers(), chain_on=chain.enabled())
                    write_atomic(tmp, json.dumps(cfg, ensure_ascii=False))
                    ok, err = check_config(tmp)
                    os.unlink(tmp)
                    if not ok:
                        self._send(400, {"error": f"sing-box rejected config: {err}"})
                        return
                    save_servers(servers, via, exp)
                    watchdog.on_hup()
                    self._send(200, {"ok": True, "count": len(servers), "via": via, "skipped": skipped, "expire": exp})
                else:
                    self._send(404, {"error": "not found"})
            except OSError as e:
                self._send(502, {"error": str(e)})
            except ValueError as e:
                self._send(400, {"error": str(e)})

        def do_PUT(self):
            if not self._auth():
                return
            route = urllib.parse.urlsplit(self.path).path
            if route != "/config":
                self._send(404, {"error": "not found"})
                return
            try:
                body = self._read_json()
                conf_path = os.path.join(CONF_DIR, "config.json")
                new_settings = dict(watchdog.s)
                if "overrides" in body and body["overrides"] is not None:
                    if is_config_externally_managed(conf_path):
                        self._send(409, {"error": "config.json is managed externally (e.g. NixOS Home Manager)"})
                        return
                    if not isinstance(body["overrides"], dict):
                        raise ValueError("overrides must be a JSON object")
                    unknown = set(body["overrides"]) - set(DEFAULTS)
                    if unknown:
                        raise ValueError(f"unknown settings keys: {', '.join(sorted(unknown))}")
                    new_settings = {**DEFAULTS, **body["overrides"]}

                # Validate candidate chain_url if provided
                c_servers = chain.load_optional_servers()
                if "chain_url" in body:
                    raw_chain = (body["chain_url"] or "").strip()
                    if raw_chain:
                        tmp_chain = os.path.join(STATE_DIR, "chain_url.check")
                        write_atomic(tmp_chain, raw_chain + "\n")
                        try:
                            c_servers, _ = chain.load_chain_servers(tmp_chain)
                        finally:
                            os.unlink(tmp_chain)
                    else:
                        c_servers = []

                # Validate full sing-box config before saving anything
                if watchdog.servers:
                    tmp_cfg = os.path.join(STATE_DIR, "config.put-check.json")
                    cfg = build_config(new_settings, watchdog.servers, "check",
                                       chain_servers=c_servers, chain_on=chain.enabled())
                    write_atomic(tmp_cfg, json.dumps(cfg, ensure_ascii=False))
                    ok, err = check_config(tmp_cfg)
                    os.unlink(tmp_cfg)
                    if not ok:
                        self._send(400, {"error": f"sing-box rejected config: {err}"})
                        return

                if "overrides" in body and body["overrides"] is not None:
                    write_atomic(conf_path, json.dumps(body["overrides"], ensure_ascii=False, indent=2) + "\n", 0o644)
                if "sub_url" in body and body["sub_url"] is not None:
                    raw_sub = body["sub_url"].strip()
                    if raw_sub:
                        write_atomic(SUB_URL_FILE, raw_sub + "\n", 0o600)
                if "chain_url" in body:
                    raw_chain = (body["chain_url"] or "").strip()
                    if raw_chain:
                        write_atomic(CHAIN_URL_FILE, raw_chain + "\n", 0o600)
                    elif os.path.exists(CHAIN_URL_FILE):
                        os.unlink(CHAIN_URL_FILE)

                watchdog.s = load_settings()
                watchdog.on_hup()
                self._send(200, {"ok": True})
            except OSError as e:
                self._send(502, {"error": str(e)})
            except ValueError as e:
                self._send(400, {"error": str(e)})

    try:
        server = http.server.ThreadingHTTPServer((listen, int(port)), Handler)
    except OSError as e:
        log.warning("control API failed to bind %s:%s: %s", listen, port, e)
        return None
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="kvn-api", daemon=True)
    thread.start()
    log.info("control API listening on http://%s:%d", listen, port)
    return server
