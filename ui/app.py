"""Isolated native desktop UI for KVN Connect (pywebview + optional pystray).

Talks to the running kvn daemon via its localhost JSON API (127.0.0.1:9096)
and falls back to local config/service helpers when the daemon is stopped, so
first-run setup and start/stop work seamlessly from the window.

Core `kvn/` has zero dependency on this directory.
"""

import json
import os
import pathlib
import sys
import threading
import time
import urllib.error

# Allow running directly as `python ui/app.py` from the repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not getattr(sys, "frozen", False) and _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from kvn import __version__, api, chain, master
from kvn.cli import api_addr, daemon_pid, main as cli_main, notify_daemon_reload, stop_daemon_gracefully
from kvn.platform import (
    is_config_externally_managed, service_control, service_install, service_uninstall,
)
from kvn.settings import (
    CHAIN_URL_FILE, CONF_DIR, DEFAULTS, LOG_FILE, STATE_DIR, SUB_URL_FILE,
    get_secret, load_settings, load_sub_meta, save_servers, write_atomic,
)
from kvn.singbox import build_config, check_config
from kvn.subscription import fetch_subscription, subscription_expiry

_BUNDLE_ROOT = getattr(sys, "_MEIPASS", None)
if _BUNDLE_ROOT:
    WEB_DIR = os.path.join(_BUNDLE_ROOT, "web")
    ICON_PATH = os.path.join(_BUNDLE_ROOT, "icon.png")
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
    WEB_DIR = os.path.join(_APP_DIR, "web")
    _LOCAL_ICON = os.path.join(_APP_DIR, "icon.png")
    ICON_PATH = _LOCAL_ICON if os.path.exists(_LOCAL_ICON) else os.path.join(_REPO_ROOT, "icon.png")


class Bridge:
    """JS-callable API exposed to the native WebView window."""

    def __init__(self):
        self._window = None

    def _call_api(self, method, path, body=None, timeout=15):
        s = load_settings()
        addr = api_addr(s)
        if not addr:
            raise OSError("control API is disabled (api_port=0)")
        return api.api_request(addr, get_secret(), method, path, body=body, timeout=timeout)

    def get_status(self):
        s = load_settings()
        pid = daemon_pid()
        if pid:
            try:
                return self._call_api("GET", "/status", timeout=10)
            except (OSError, urllib.error.URLError):
                pass
        return {
            "version": __version__,
            "running": bool(pid),
            "pid": pid,
            "state": "starting" if pid else "stopped",
            "listen": s["listen"],
            "port": s["port"],
            "proxy": f"{s['listen']}:{s['port']}",
            "proxy_ok": False,
            "server": None,
            "delay_ms": 0,
            "mode": "auto",
            "master": {
                "configured": bool(s["master"]),
                "pattern": s["master"],
                "enabled": master.enabled(s),
                "max_ratio": s["master_max_ratio"],
                "max_lag": s["master_max_lag"],
            },
            "chain": {
                "configured": chain.configured(),
                "enabled": chain.enabled(),
                "via": s["chain_via"],
                "port": s.get("chain_port"),
                "active": None,
                "works": None,
                "latency_ms": None,
            },
            "subscription": load_sub_meta(),
            "has_sub_url": os.path.exists(SUB_URL_FILE),
            "managed_externally": is_config_externally_managed(os.path.join(CONF_DIR, "config.json")),
        }

    def get_servers(self):
        if not daemon_pid():
            return {"current": None, "mode": "stopped", "servers": []}
        try:
            return self._call_api("GET", "/servers", timeout=10)
        except Exception as e:
            return {"current": None, "mode": "error", "servers": [], "error": str(e)}

    def get_config(self):
        if daemon_pid():
            try:
                return self._call_api("GET", "/config", timeout=5)
            except Exception:
                pass
        s = load_settings()
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
            "effective": s,
            "overrides": overrides,
            "defaults": dict(DEFAULTS),
            "has_sub_url": os.path.exists(SUB_URL_FILE),
            "has_chain_url": os.path.exists(CHAIN_URL_FILE),
            "chain_servers": [
                {"tag": x["tag"], "type": x["type"], "server": x["server"], "server_port": x["server_port"]}
                for x in c_servers
            ],
            "managed_externally": is_config_externally_managed(conf_path),
            "platform": sys.platform,
        }

    def save_config(self, payload):
        """Saves settings / sub_url / chain_url via daemon API or directly when stopped."""
        if daemon_pid():
            try:
                return self._call_api("PUT", "/config", payload, timeout=15)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                try:
                    msg = json.loads(detail).get("error", detail)
                except ValueError:
                    msg = detail
                return {"ok": False, "error": msg}
            except OSError:
                pass

        # Offline save when daemon is stopped
        try:
            conf_path = os.path.join(CONF_DIR, "config.json")
            if "overrides" in payload and payload["overrides"] is not None:
                if is_config_externally_managed(conf_path):
                    return {"ok": False, "error": "config.json is managed externally (e.g. NixOS Home Manager)"}
                unknown = set(payload["overrides"]) - set(DEFAULTS)
                if unknown:
                    return {"ok": False, "error": f"unknown keys: {', '.join(sorted(unknown))}"}
                write_atomic(conf_path, json.dumps(payload["overrides"], ensure_ascii=False, indent=2) + "\n", 0o644)
            if payload.get("sub_url"):
                write_atomic(SUB_URL_FILE, payload["sub_url"].strip() + "\n", 0o600)
            if "chain_url" in payload:
                raw_chain = (payload["chain_url"] or "").strip()
                if raw_chain:
                    tmp = os.path.join(STATE_DIR, "chain_url.check")
                    write_atomic(tmp, raw_chain + "\n")
                    try:
                        chain.load_chain_servers(tmp)
                    finally:
                        os.unlink(tmp)
                    write_atomic(CHAIN_URL_FILE, raw_chain + "\n", 0o600)
                elif os.path.exists(CHAIN_URL_FILE):
                    os.unlink(CHAIN_URL_FILE)
            return {"ok": True}
        except (OSError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def start_service(self):
        s = load_settings()
        if not os.path.exists(SUB_URL_FILE):
            return {"ok": False, "error": "Set your subscription URL in Settings first"}
        try:
            service_control("start", stop_fn=lambda: stop_daemon_gracefully(s), pid_fn=daemon_pid)
            for _ in range(30):
                if daemon_pid():
                    try:
                        self._call_api("GET", "/health", timeout=1)
                        return {"ok": True}
                    except OSError:
                        pass
                time.sleep(0.5)
            return {"ok": True, "warning": "started; daemon is still warming up"}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def stop_service(self):
        s = load_settings()
        try:
            service_control("stop", stop_fn=lambda: stop_daemon_gracefully(s), pid_fn=daemon_pid)
            return {"ok": True}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def restart_service(self):
        s = load_settings()
        try:
            service_control("restart", stop_fn=lambda: stop_daemon_gracefully(s), pid_fn=daemon_pid)
            return {"ok": True}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def connect_now(self):
        try:
            return self._call_api("POST", "/connect", {}, timeout=180)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def use_server(self, name):
        try:
            return self._call_api("POST", "/use", {"server": name}, timeout=10)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def set_master(self, enabled):
        if daemon_pid():
            try:
                return self._call_api("POST", "/master", {"enabled": bool(enabled)}, timeout=10)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        master.set_enabled(bool(enabled))
        return {"ok": True, "enabled": master.enabled(load_settings())}

    def set_chain(self, enabled):
        if daemon_pid():
            try:
                return self._call_api("POST", "/chain", {"enabled": bool(enabled)}, timeout=10)
            except Exception as e:
                return {"ok": False, "error": str(e)}
        chain.set_enabled(bool(enabled))
        return {"ok": True, "enabled": chain.enabled()}

    def fetch_subscription(self):
        if daemon_pid():
            try:
                return self._call_api("POST", "/fetch", {}, timeout=30)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                return {"ok": False, "error": detail}
            except OSError:
                pass
        s = load_settings()
        try:
            servers, skipped, headers, via = fetch_subscription(s, [None, f"http://{s['listen']}:{s['port']}"])
            exp = subscription_expiry(headers)
            tmp = os.path.join(STATE_DIR, "config.ui-check.json")
            cfg = build_config(s, servers, "check",
                               chain_servers=chain.load_optional_servers(), chain_on=chain.enabled())
            write_atomic(tmp, json.dumps(cfg, ensure_ascii=False))
            ok, err = check_config(tmp)
            os.unlink(tmp)
            if not ok:
                return {"ok": False, "error": f"sing-box rejected config: {err}"}
            save_servers(servers, via, exp)
            pid = daemon_pid()
            if pid:
                notify_daemon_reload(s, pid)
            return {"ok": True, "count": len(servers), "via": via, "skipped": skipped, "expire": exp}
        except (OSError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    def install_autostart(self):
        try:
            return {"ok": True, "message": service_install(LOG_FILE)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def uninstall_autostart(self):
        try:
            return {"ok": True, "message": service_uninstall()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_logs(self, lines=120):
        if daemon_pid():
            try:
                return self._call_api("GET", f"/logs?lines={int(lines)}", timeout=5)
            except Exception:
                pass
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                    return {"lines": [ln.rstrip("\n") for ln in f.readlines()[-int(lines):]]}
            except OSError:
                pass
        return {"lines": []}


def _start_tray(bridge, window):
    try:
        import pystray
        from PIL import Image
    except ImportError:
        return None

    if os.path.exists(ICON_PATH):
        img = Image.open(ICON_PATH).convert("RGBA").resize((64, 64))
    else:
        img = Image.new("RGBA", (64, 64), (46, 160, 67, 255))

    def on_show(_icon, _item):
        window.show()

    def on_reconnect(_icon, _item):
        threading.Thread(target=bridge.connect_now, daemon=True).start()

    def on_quit(icon, _item):
        icon.stop()
        window.destroy()

    menu = pystray.Menu(
        pystray.MenuItem("Open KVN Connect", on_show, default=True),
        pystray.MenuItem("Reconnect", on_reconnect),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit UI", on_quit),
    )
    icon = pystray.Icon("kvn", img, "KVN Connect", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    # Allow standalone bundled executable to spawn the background daemon (`KVN-Connect run`)
    if args and args[0] in ("run", "status", "fetch", "connect", "use", "master", "chain",
                            "servers", "logs", "init", "install", "uninstall", "start", "stop", "restart"):
        return cli_main(args)

    try:
        import webview
    except ImportError:
        print("kvn-ui requires pywebview (install with: pip install 'kvn[ui]')", file=sys.stderr)
        return 1

    bridge = Bridge()
    index_uri = pathlib.Path(os.path.join(WEB_DIR, "index.html")).resolve().as_uri()
    window = webview.create_window(
        "KVN Connect",
        url=index_uri,
        js_api=bridge,
        width=460,
        height=720,
        min_size=(400, 560),
        background_color="#0d1117",
    )
    bridge._window = window

    tray = None
    if "--no-tray" not in args:
        tray = _start_tray(bridge, window)

    try:
        webview.start(debug="--debug" in args)
    finally:
        if tray:
            try:
                tray.stop()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
