"""Command line. Commands map to what you want when something is off:

  status          is it running, does the proxy work, which server, how old is the subscription
  connect         re-test all servers now and switch to the best one
  use             pin one server by hand, or `use auto` to hand control back to the watchdog
  master          show / turn on / turn off preferring the master server
  fetch           download the subscription now and apply it
  start/stop/restart
                  control the background service (systemd user unit)
  logs            follow the service log
  servers         every server with its last measured delay
  run             the daemon itself (what the service runs)
"""

import argparse
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time

from . import __version__, master
from .clash import Clash, top_list
from .daemon import Watchdog
from .net import probe
from .settings import (
    PID_FILE, STATE_DIR, SUB_URL_FILE, get_secret, load_settings, load_sub_meta, save_servers, write_atomic,
)
from .singbox import build_config, check_config
from .subscription import fetch_subscription, subscription_expiry

SERVICE = "kvn"


# ---------------------------------------------------------------- helpers

def daemon_pid():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def clash_for(settings):
    return Clash(settings["clash_api"], get_secret())


def proxy_url(settings):
    return f"http://{settings['listen']}:{settings['port']}"


def ago(ts):
    s = max(0, time.time() - ts)
    if s < 120:
        return f"{s:.0f} s ago"
    if s < 7200:
        return f"{s / 60:.0f} min ago"
    if s < 2 * 86400:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


def fail(msg, hint=None):
    print(f"kvn: {msg}", file=sys.stderr)
    if hint:
        print(f"     {hint}", file=sys.stderr)
    return 1


def systemctl(*args):
    if not shutil.which("systemctl"):
        raise OSError("systemctl not found; run `kvn run` under your own supervisor")
    return subprocess.run(["systemctl", "--user", *args], check=False)


# ---------------------------------------------------------------- commands

def cmd_status(settings, args):
    pid = daemon_pid()
    print(f"service       {'running (pid %d)' % pid if pid else 'not running  → kvn start'}")

    if pid:
        works = probe(settings["probe_urls"], proxy_url(settings))
        print(f"proxy         {settings['listen']}:{settings['port']}  {'works' if works else 'NOT working  → kvn connect'}")
        try:
            clash = clash_for(settings)
            sel = clash.selector().get("now")
            if sel and sel != "auto":
                delay = clash.last_delay(sel)
                print(f"server        {sel}{f'  ({delay} ms)' if delay else ''}  (pinned → kvn use auto)")
            else:
                auto_sel = clash.auto_selected()
                now = clash.current()
                delay = clash.last_delay(now) if now else 0
                how = "master" if auto_sel != "fastest" else "auto"
                print(f"server        {now or 'still choosing'}{f'  ({delay} ms)' if delay else ''}  ({how})")
        except OSError:
            print("server        unknown, sing-box is not answering  → kvn restart")

    meta = load_sub_meta()
    if meta:
        line = f"{meta.get('count', '?')} servers, fetched {ago(meta['fetched_at'])} ({meta.get('via', '?')})"
        exp = meta.get("expire")
        if exp:
            days = (exp - time.time()) / 86400
            line += f", expires {time.strftime('%F', time.localtime(exp))}"
            line += "  EXPIRED" if days < 0 else f" (in {days:.0f} d)"
        print(f"subscription  {line}")
    elif not os.path.exists(SUB_URL_FILE):
        print(f"subscription  no URL  → put it into {SUB_URL_FILE}")
    else:
        print("subscription  not fetched yet  → kvn fetch")
    return 0


def cmd_connect(settings, args):
    """Re-test every server and let urltest switch to the fastest live one."""
    if not daemon_pid():
        return fail("the service is not running", "start it: kvn start")
    clash = clash_for(settings)
    try:
        before = clash.current()
        print("testing all servers, can take up to a minute…", flush=True)
        alive = clash.test_all(settings["test_url"])
        total = len(clash.get("/proxies/fastest").get("all", []))
        time.sleep(1)  # urltest re-picks right after the group test
        master.steer(settings, clash)
        now = clash.current()
        delay = clash.last_delay(now) if now else 0
    except OSError as e:
        return fail(f"sing-box is not answering ({e})", "try: kvn restart")
    if not alive:
        return fail(f"0 of {total} servers answer",
                    "the server list may be stale: kvn fetch  (or check your network)")
    switched = f"{before} → {now}" if before and before != now else now
    print(f"connected: {switched}  ({delay} ms)")
    print(f"alive {len(alive)}/{total}; best: {top_list(alive)}")
    if not probe(settings["probe_urls"], proxy_url(settings)):
        return fail("servers answer, but the proxy still does not", "see: kvn logs")
    return 0


def resolve_server(name, members):
    """Map a user-typed name onto a selector member.

    Exact match wins; otherwise a unique case-insensitive substring (names carry
    flags and spaces, so typing `nl` or `tokyo` is enough). Returns the tag, None
    when nothing matches, or the list of candidates when it's ambiguous.
    """
    if name in members:
        return name
    hits = [m for m in members if name.lower() in m.lower()]
    if len(hits) == 1:
        return hits[0]
    return hits or None


def cmd_use(settings, args):
    """Pin routing to one server, or 'auto' to hand control back to the watchdog."""
    if not daemon_pid():
        return fail("the service is not running", "start it: kvn start")
    clash = clash_for(settings)
    try:
        members = clash.selector().get("all", [])
    except OSError as e:
        return fail(f"sing-box is not answering ({e})", "try: kvn restart")
    target = resolve_server(args.server, members)
    if target is None:
        return fail(f"no server matches {args.server!r}", "see the names: kvn servers")
    if isinstance(target, list):
        return fail(f"{args.server!r} matches several servers:\n     " + "\n     ".join(target),
                    "use a more specific name")
    try:
        clash.select(target)
    except OSError as e:
        return fail(f"could not switch ({e})", "try: kvn restart")
    if target == "auto":
        try:
            master.steer(settings, clash)
        except OSError:
            pass
        print("auto: the watchdog picks the fastest live server (or master) and switches on failure")
        return 0
    try:
        delay = clash.last_delay(target)
    except OSError:
        delay = 0
    print(f"pinned to {target}{f'  ({delay} ms)' if delay else ''}")
    print("the watchdog won't switch or recover while pinned  → kvn use auto")
    return 0


def cmd_fetch(settings, args):
    """Download the subscription right now, validate it and hand it to the service."""
    if not os.path.exists(SUB_URL_FILE):
        return fail(f"no subscription URL in {SUB_URL_FILE}")
    # Direct first: Blanc refuses the subscription from its own exits.
    try:
        servers, skipped, headers, via = fetch_subscription(settings, [None, proxy_url(settings)])
    except OSError as e:
        return fail(f"could not download the subscription: {e}")

    print(f"downloaded via {via}: {len(servers)} usable servers")
    if skipped:
        print("skipped:", ", ".join(f"{k} ×{n}" for k, n in skipped.items()))
    exp = subscription_expiry(headers)
    if exp:
        print(f"expires {time.strftime('%F %H:%M', time.localtime(exp))} (in {(exp - time.time()) / 86400:.0f} d)")
    if args.verbose:
        exclude = re.compile(settings["exclude"]) if settings["exclude"] else None
        for s in servers:
            kind = "reality" if s.get("tls", {}).get("reality") else "tls" if s.get("tls") else "plain"
            mark = "  (excluded)" if exclude and exclude.search(s["tag"]) else ""
            print(f"  {kind:<7} {s.get('transport', {}).get('type', 'tcp'):<11} {s['tag']}{mark}")

    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(STATE_DIR, "config.check.json")
    write_atomic(path, json.dumps(build_config(settings, servers, "check"), ensure_ascii=False))
    ok, err = check_config(path)
    os.unlink(path)
    if not ok:
        return fail(f"sing-box rejects the generated config, nothing applied:\n{err}")
    if args.dry_run:
        print("config OK (dry run, nothing applied)")
        return 0

    save_servers(servers, via, exp)
    pid = daemon_pid()
    if pid:
        os.kill(pid, signal.SIGHUP)
        print("applied, the service is re-testing servers now; check with: kvn status")
    else:
        print("saved; the service is not running → kvn start")
    return 0


def service_cmd(action):
    def run(settings, args):
        try:
            r = systemctl(action, SERVICE)
        except OSError as e:
            return fail(str(e))
        if r.returncode != 0:
            return fail(f"systemctl --user {action} {SERVICE} failed", "see: kvn logs")
        if action == "stop":
            print("stopped; the proxy is down until: kvn start")
            return 0
        # Wait until sing-box answers so the next command doesn't race the startup.
        clash = clash_for(settings)
        for _ in range(30):
            try:
                clash.get("/version", timeout=1)
                print(f"{action}ed; picking the fastest server takes 1–2 min; check with: kvn status")
                return 0
            except OSError:
                time.sleep(1)
        return fail("the service started but sing-box is not up yet", "see: kvn logs")
    return run


def cmd_logs(settings, args):
    if not shutil.which("journalctl"):
        return fail("journalctl not found")
    cmd = ["journalctl", "--user", "-u", SERVICE, "-n", str(args.lines)]
    if not args.no_follow:
        cmd.append("-f")
    try:
        return subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        return 0


def cmd_servers(settings, args):
    if not daemon_pid():
        return fail("the service is not running", "start it: kvn start")
    clash = clash_for(settings)
    try:
        _, delays = clash.delays()
        sel = clash.selector().get("now")
        auto_sel = clash.auto_selected()
        routed = clash.current()
    except OSError as e:
        return fail(f"sing-box is not answering ({e})", "try: kvn restart")
    for tag, d in sorted(delays.items(), key=lambda kv: (kv[1] <= 0, kv[1])):
        print(f"{'*' if tag == routed else ' '} {d if d > 0 else 'dead':>6}  {tag}")
    if sel and sel != "auto":
        print(f"\npinned to {sel}  → kvn use auto")
    elif auto_sel != "fastest":
        print(f"\nmaster: * is the preferred server  → kvn master off / kvn use <name>")
    else:
        print("\nauto: * is the current pick  → kvn use <name> to pin one")
    return 0


def cmd_master(settings, args):
    if not settings["master"]:
        return fail("no master server configured", 'set "master" (a regex) in config.json')
    if args.state:
        master.set_enabled(args.state == "on")
    on = master.enabled(settings)
    rule = f"lag ≤ {settings['master_max_ratio']}× or ≤ {settings['master_max_lag']} ms behind the leader"
    print(f"master        {'on' if on else 'off'}, /{settings['master']}/, {rule}")
    if not daemon_pid():
        print("service       not running  → kvn start")
        return 0
    clash = clash_for(settings)
    try:
        if args.state:
            master.steer(settings, clash)
        _, delays = clash.delays()
        sel = clash.selector().get("now")
        auto_sel = clash.auto_selected()
        now = clash.current()
    except OSError as e:
        return fail(f"sing-box is not answering ({e})", "try: kvn restart")
    tag, delay, best, ok = master.evaluate(settings, delays)
    if tag:
        verdict = "good enough" if ok else "too slow"
        print(f"candidate     {tag}  ({delay} ms, leader {best} ms): {verdict}")
    elif not any(re.search(settings["master"], t) for t in delays):
        print(f"candidate     no server matches /{settings['master']}/  → kvn servers")
    else:
        print("candidate     dead or not tested yet")
    if sel and sel != "auto":
        print(f"routing via   {sel}  (pinned → kvn use auto)")
    else:
        print(f"routing via   {now or 'still choosing'}  ({'master' if auto_sel != 'fastest' else 'urltest pick'})")
    return 0


def cmd_run(settings, args):
    Watchdog(settings).run()
    return 0


# ---------------------------------------------------------------- entry point

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="kvn", description="Keeps a sing-box proxy on a BlancVPN subscription alive.",
        epilog="Something broke? Start with `kvn status`, it tells what to run next.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    sub.add_parser("status", help="is it running, does the proxy work, current server, subscription age")
    sub.add_parser("connect", help="re-test all servers now and switch to the best one")
    u = sub.add_parser("use", help="pin routing to one server, or 'auto' for fastest-server autopilot")
    u.add_argument("server", metavar="SERVER|auto", help="server name (a substring is enough) or 'auto'")
    m = sub.add_parser("master", help="prefer the master server while it's good enough (on/off), or show it")
    m.add_argument("state", nargs="?", choices=["on", "off"])
    f = sub.add_parser("fetch", help="download the subscription now and apply it")
    f.add_argument("-n", "--dry-run", action="store_true", help="only download and validate")
    f.add_argument("-v", "--verbose", action="store_true", help="list every server")
    sub.add_parser("start", help="start the background service")
    sub.add_parser("stop", help="stop the service (the proxy goes down)")
    sub.add_parser("restart", help="restart the service and sing-box")
    lg = sub.add_parser("logs", help="follow the service log")
    lg.add_argument("-n", "--lines", type=int, default=50)
    lg.add_argument("--no-follow", action="store_true")
    sub.add_parser("servers", help="every server with its last measured delay")
    sub.add_parser("run", help="run the daemon in the foreground (what the service runs)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    handlers = {
        "status": cmd_status, "connect": cmd_connect, "use": cmd_use, "master": cmd_master, "fetch": cmd_fetch,
        "logs": cmd_logs, "servers": cmd_servers, "run": cmd_run,
        "start": service_cmd("start"), "stop": service_cmd("stop"), "restart": service_cmd("restart"),
    }
    try:
        return handlers[args.cmd](load_settings(), args)
    except KeyboardInterrupt:
        return 130
