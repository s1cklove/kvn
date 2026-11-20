"""Optional chain layer (`kvn chain`): route traffic through a second exit (e.g. personal VPS).

Reads one or more vless:// links from ~/.config/kvn/chain_url (chmod 600).
When present, sing-box chains those server(s) on top of the subscription layer
(via `detour: "select"` by default, or `auto` / `direct`) inside the single kvn
process. If `chain_port` is set, a dedicated mixed proxy listens on that port
while the main port stays on the subscription; otherwise `kvn chain on|off`
toggles the main port on the fly via the Clash API.
"""

import logging
import os
import time

from .net import http_get
from .settings import CHAIN_OFF_FILE, CHAIN_URL_FILE, write_atomic
from .subscription import parse_subscription

log = logging.getLogger("kvn")

CHAIN_TAG_PREFIX = "chain:"


def configured(path=None):
    return os.path.exists(path or CHAIN_URL_FILE)


def enabled(path=None):
    return configured(path) and not os.path.exists(CHAIN_OFF_FILE)


def set_enabled(on):
    if on:
        try:
            os.unlink(CHAIN_OFF_FILE)
        except FileNotFoundError:
            pass
    else:
        write_atomic(CHAIN_OFF_FILE, "")


def display_tag(tag):
    if tag and tag.startswith(CHAIN_TAG_PREFIX):
        return tag[len(CHAIN_TAG_PREFIX):]
    return tag


def chain_proxy_url(settings):
    port = settings["chain_port"] or settings["port"]
    return f"http://{settings['listen']}:{port}"


def load_chain_servers(path=None):
    """Loads chain exit link(s) from path (default: ~/.config/kvn/chain_url).

    Returns (servers, skipped). Blank lines and `#` comments are ignored.
    """
    src = path or CHAIN_URL_FILE
    with open(src, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"no links found in {src}")
    servers, skipped = parse_subscription("\n".join(lines).encode("utf-8"))
    if not servers:
        raise ValueError(f"no usable servers in {src} (skipped: {skipped})")
    return servers, skipped


def load_optional_servers(path=None):
    """Loads chain servers if chain_url exists; logs and returns [] on error."""
    src = path or CHAIN_URL_FILE
    if not os.path.exists(src):
        return []
    try:
        servers, skipped = load_chain_servers(src)
    except (OSError, ValueError) as e:
        log.warning("ignoring invalid chain config (%s): %s", src, e)
        return []
    if skipped:
        log.info("chain skipped entries: %s", skipped)
    return servers


def probe_latency(urls, proxy, timeout=8):
    """Returns latency in ms of the first probe URL returning HTTP < 500, or None."""
    for url in urls:
        t0 = time.monotonic()
        try:
            status, _, _ = http_get(url, proxy=proxy, timeout=timeout,
                                    headers={"User-Agent": "Mozilla/5.0"})
            if status < 500:
                return max(1, int((time.monotonic() - t0) * 1000))
        except (OSError, ValueError):
            pass
    return None


def steer(clash):
    """Points the 'out' selector at the chain target or 'select'.

    Returns (before, after); raises OSError if sing-box has no 'out' selector.
    """
    out = clash.out_selector()
    before = out.get("now")
    choices = out.get("all", [])
    target = next((c for c in choices if c != "select"), None)
    want = target if (enabled() and target) else "select"
    if want != before:
        clash.select_out(want)
    return before, want
