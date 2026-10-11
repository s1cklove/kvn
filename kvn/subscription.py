import base64
import hashlib
import json
import re
import urllib.parse

from .net import http_get
from .settings import SUB_URL_FILE

RESERVED_TAGS = {"auto", "direct", "mixed-in"}


class Unsupported(Exception):
    pass


def decode_subscription(body):
    text = body.decode("utf-8", "replace").strip()
    if "://" not in text:
        compact = re.sub(r"\s+", "", text)
        compact += "=" * (-len(compact) % 4)
        try:
            text = base64.b64decode(compact.replace("-", "+").replace("_", "/")).decode("utf-8", "replace")
        except ValueError as e:
            raise ValueError(f"subscription is neither links nor base64: {e}")
    return [line.strip() for line in text.splitlines() if "://" in line]


def parse_vless(uri):
    u = urllib.parse.urlsplit(uri)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
    if not u.hostname or not u.port or not u.username:
        raise Unsupported("missing host/port/uuid")

    out = {
        "type": "vless",
        "tag": urllib.parse.unquote(u.fragment).strip() or f"{u.hostname}:{u.port}",
        "server": u.hostname,
        "server_port": u.port,
        "uuid": urllib.parse.unquote(u.username),
        "packet_encoding": "xudp",
    }
    if q.get("flow"):
        out["flow"] = q["flow"]

    security = q.get("security", "none")
    if security in ("tls", "reality"):
        tls = {"enabled": True}
        sni = q.get("sni") or q.get("host") or u.hostname
        tls["server_name"] = sni
        fp = q.get("fp") or ("chrome" if security == "reality" else "")
        if fp:
            tls["utls"] = {"enabled": True, "fingerprint": fp}
        if q.get("alpn"):
            tls["alpn"] = q["alpn"].split(",")
        if q.get("allowInsecure") in ("1", "true") or q.get("insecure") in ("1", "true"):
            tls["insecure"] = True
        if security == "reality":
            if not q.get("pbk"):
                raise Unsupported("reality without pbk")
            tls["reality"] = {"enabled": True, "public_key": q["pbk"], "short_id": q.get("sid", "")}
        out["tls"] = tls
    elif security != "none":
        raise Unsupported(f"security={security}")

    net = q.get("type", "tcp")
    if net in ("tcp", "raw"):
        if q.get("headerType", "none") not in ("none", ""):
            raise Unsupported(f"tcp headerType={q['headerType']}")
    elif net == "ws":
        path = q.get("path", "/")
        t = {"type": "ws", "path": path}
        m = re.search(r"[?&]ed=(\d+)", path)
        if m:
            t["path"] = re.sub(r"[?&]ed=\d+", "", path) or "/"
            t["max_early_data"] = int(m.group(1))
            t["early_data_header_name"] = "Sec-WebSocket-Protocol"
        if q.get("host"):
            t["headers"] = {"Host": q["host"]}
        out["transport"] = t
    elif net == "grpc":
        out["transport"] = {"type": "grpc", "service_name": q.get("serviceName", "")}
    elif net == "httpupgrade":
        out["transport"] = {"type": "httpupgrade", "path": q.get("path", "/"), "host": q.get("host", "")}
    elif net in ("http", "h2"):
        t = {"type": "http", "path": q.get("path", "/")}
        if q.get("host"):
            t["host"] = q["host"].split(",")
        out["transport"] = t
    else:
        raise Unsupported(f"transport {net}")
    return out


PARSERS = {"vless": parse_vless}


def parse_subscription(body):
    """Returns (outbounds, skipped) where skipped maps reason -> count."""
    servers, skipped, seen = [], {}, set()
    for line in decode_subscription(body):
        scheme = line.split("://", 1)[0].lower()
        try:
            if scheme not in PARSERS:
                raise Unsupported(f"scheme {scheme}")
            ob = PARSERS[scheme](line)
        except (Unsupported, ValueError) as e:
            skipped[str(e)] = skipped.get(str(e), 0) + 1
            continue
        tag, n = ob["tag"], 2
        if tag in RESERVED_TAGS:
            tag = ob["tag"] = f"srv {tag}"
        while ob["tag"] in seen:
            ob["tag"] = f"{tag} #{n}"
            n += 1
        seen.add(ob["tag"])
        servers.append(ob)
    return servers, skipped


def fetch_subscription(settings, proxies):
    """Tries each proxy in order (None = direct). Returns (servers, skipped, headers, via)."""
    with open(SUB_URL_FILE) as f:
        url = f.read().strip()
    errors = []
    for proxy in proxies:
        via = "proxy" if proxy else "direct"
        try:
            status, headers, body = http_get(url, proxy=proxy, timeout=20,
                                             headers={"User-Agent": settings["user_agent"]})
            if status != 200:
                raise OSError(f"HTTP {status}")
            servers, skipped = parse_subscription(body)
            if not servers:
                raise ValueError(f"no usable servers (skipped: {skipped})")
            return servers, skipped, headers, via
        except (OSError, ValueError) as e:
            errors.append(f"{via}: {e}")
    raise OSError("; ".join(errors))


IDENTITY_FIELDS = ("tag", "server", "server_port", "uuid", "flow")


def servers_digest(servers, full=True):
    """full=False ignores what Blanc rotates on every fetch (SNI, short_id, ws path/host)."""
    def key(o):
        if full:
            return o
        return {**{k: o.get(k) for k in IDENTITY_FIELDS},
                "transport": o.get("transport", {}).get("type"),
                "reality": bool(o.get("tls", {}).get("reality"))}
    blob = json.dumps(sorted((key(o) for o in servers), key=lambda o: o["tag"]), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def subscription_expiry(headers):
    m = re.search(r"expire=(\d+)", headers.get("subscription-userinfo", "") if headers else "")
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None
