"""Standalone VPS proxy (`kvn vps`), kept separate from the subscription daemon.

Reads one or more vless:// links from ~/.config/kvn/vps_url (chmod 600),
generates ~/.local/state/kvn/vps.config.json and execs sing-box directly so
systemd (or the foreground terminal) supervises it with zero wrapper overhead.
"""

import json
import os
import socket
import time

from .net import http_get
from .settings import SING_BOX, STATE_DIR, VPS_BOX_CONFIG, VPS_PID_FILE, VPS_URL_FILE, write_atomic
from .singbox import build_vps_config, check_config
from .subscription import parse_subscription

VPS_SERVICE = "kvn-vps"


def vps_proxy_url(settings):
    return f"http://{settings['vps_listen']}:{settings['vps_port']}"


def load_vps_servers(path=None):
    """Loads standalone VPS link(s) from path (default: ~/.config/kvn/vps_url).

    Returns (servers, skipped). Blank lines and `#` comments are ignored.
    """
    src = path or VPS_URL_FILE
    with open(src, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"no links found in {src}")
    servers, skipped = parse_subscription("\n".join(lines).encode("utf-8"))
    if not servers:
        raise ValueError(f"no usable servers in {src} (skipped: {skipped})")
    return servers, skipped


def vps_pid():
    try:
        with open(VPS_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().decode("utf-8", "replace")
        if cmd and "sing-box" not in cmd and "kvn" not in cmd:
            return None
    except OSError:
        pass
    return pid


def port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_free(host, port):
    try:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


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


def prepare_vps_config(settings, path=None):
    """Builds and validates sing-box config into a temp file.

    Returns (servers, skipped, tmp_path). Caller is responsible for removing or
    atomically replacing tmp_path.
    """
    servers, skipped = load_vps_servers(path)
    cfg = json.dumps(build_vps_config(settings, servers), ensure_ascii=False, indent=1)
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = VPS_BOX_CONFIG + ".new"
    write_atomic(tmp, cfg)
    ok, err = check_config(tmp)
    if not ok:
        os.unlink(tmp)
        raise RuntimeError(f"sing-box rejects the generated config:\n{err}")
    return servers, skipped, tmp


def run_vps(settings):
    """Generates, validates and installs VPS config, then execs sing-box."""
    if not port_free(settings["vps_listen"], settings["vps_port"]):
        raise OSError(f"port {settings['vps_listen']}:{settings['vps_port']} is already in use")
    _, _, tmp = prepare_vps_config(settings)
    os.replace(tmp, VPS_BOX_CONFIG)
    write_atomic(VPS_PID_FILE, str(os.getpid()), 0o644)
    os.execvp(SING_BOX, [SING_BOX, "run", "--disable-color", "-c", VPS_BOX_CONFIG, "-D", STATE_DIR])
