import argparse
import json
import logging
import os
import re
import signal
import sys
import time

from . import __version__
from .clash import Clash
from .daemon import Watchdog
from .settings import PID_FILE, STATE_DIR, get_secret, load_settings, write_atomic
from .singbox import build_config, check_config
from .subscription import fetch_subscription, subscription_expiry


def cmd_check(settings, args):
    """Fetch + parse the subscription and validate the resulting sing-box config. Prints no secrets."""
    proxies = [None] if not args.via_proxy else [f"http://{settings['listen']}:{settings['port']}"]
    servers, skipped, headers, via = fetch_subscription(settings, proxies)
    print(f"fetched via {via}: {len(servers)} usable servers")
    if skipped:
        print("skipped:", skipped)
    exp = subscription_expiry(headers)
    if exp:
        print("expires:", time.strftime("%F %T", time.localtime(exp)))
    kinds = {}
    for s in servers:
        k = (s.get("tls", {}).get("reality") and "reality" or s.get("tls") and "tls" or "plain",
             s.get("transport", {}).get("type", "tcp"), s.get("flow", "-"))
        kinds[k] = kinds.get(k, 0) + 1
    for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d} × security={k[0]} transport={k[1]} flow={k[2]}")
    exclude = re.compile(settings["exclude"]) if settings["exclude"] else None
    for s in servers:
        mark = "  (excluded)" if exclude and exclude.search(s["tag"]) else ""
        print(f"    {s['tag']}{mark}")
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(STATE_DIR, "config.check.json")
    write_atomic(path, json.dumps(build_config(settings, servers, "check"), ensure_ascii=False))
    ok, err = check_config(path)
    os.unlink(path)
    print("sing-box check:", "OK" if ok else f"FAILED\n{err}")
    return 0 if ok else 1


def cmd_status(settings, args):
    clash = Clash(settings["clash_api"], get_secret())
    if args.test:
        alive = clash.test_all(settings["test_url"])
        print(f"alive: {len(alive)}/{len(clash.get('/proxies/auto')['all'])}")
    now, delays = clash.delays()
    print(f"current: {now}\n")
    for tag, d in sorted(delays.items(), key=lambda kv: (kv[1] <= 0, kv[1])):
        print(f"  {'*' if tag == now else ' '} {d if d > 0 else 'dead':>6}  {tag}")
    return 0


def cmd_update(settings, args):
    try:
        with open(PID_FILE) as f:
            os.kill(int(f.read()), signal.SIGUSR1)
    except (OSError, ValueError) as e:
        print(f"daemon is not running? ({e})", file=sys.stderr)
        return 1
    print("asked the daemon to refresh the subscription and re-test servers; see journalctl --user -u kvn")
    return 0


def main():
    p = argparse.ArgumentParser(prog="kvn")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run the daemon (systemd)")
    c = sub.add_parser("check", help="fetch and validate the subscription")
    c.add_argument("--via-proxy", action="store_true", help="fetch through the local proxy")
    st = sub.add_parser("status", help="show current server and delays")
    st.add_argument("-t", "--test", action="store_true", help="re-test all servers first")
    sub.add_parser("update", help="force subscription refresh and server re-test")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    settings = load_settings()
    if args.cmd == "run":
        Watchdog(settings).run()
        return 0
    return {"check": cmd_check, "status": cmd_status, "update": cmd_update}[args.cmd](settings, args)
