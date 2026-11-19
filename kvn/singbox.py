import os
import re
import subprocess

from .settings import RULES_DIR, SING_BOX


def _route_rules(bypass_russia, rulesets):
    rules = [{"action": "sniff"}, {"ip_is_private": True, "outbound": "direct"}]
    rule_sets = []
    if bypass_russia:
        rules.append({"domain_suffix": ["ru", "su", "xn--p1ai"], "outbound": "direct"})
        for tag in rulesets:
            path = os.path.join(RULES_DIR, tag + ".srs")
            if os.path.exists(path):
                rule_sets.append({"type": "local", "tag": tag, "format": "binary", "path": path})
        if rule_sets:
            rules.append({"rule_set": [r["tag"] for r in rule_sets], "outbound": "direct"})
    return rules, rule_sets


def build_config(settings, servers, secret):
    exclude = re.compile(settings["exclude"]) if settings["exclude"] else None
    chosen = [s for s in servers if not (exclude and exclude.search(s["tag"]))] or servers
    tags = [s["tag"] for s in chosen]

    rules, rule_sets = _route_rules(settings["bypass_russia"], settings["rulesets"])

    return {
        "log": {"level": "warn", "timestamp": False},
        "inbounds": [{"type": "mixed", "tag": "mixed-in",
                      "listen": settings["listen"], "listen_port": settings["port"]}],
        "outbounds": [
            # "select" is what routing actually uses; its default member is "auto".
            # `kvn use <server>` pins it to one exit (and pauses the watchdog);
            # `kvn use auto` hands control back.
            {"type": "selector", "tag": "select", "outbounds": ["auto", *tags],
             "default": "auto", "interrupt_exist_connections": True},
            # "auto" picks either "fastest" (urltest) or the master server while it
            # keeps up (see master.py). No interrupting on a switch, so flipping
            # between master and urltest's pick doesn't kill open connections.
            {"type": "selector", "tag": "auto", "outbounds": ["fastest", *tags],
             "default": "fastest", "interrupt_exist_connections": False},
            {"type": "urltest", "tag": "fastest", "outbounds": tags,
             "url": settings["test_url"], "interval": settings["urltest_interval"],
             "tolerance": settings["tolerance"], "interrupt_exist_connections": True},
            *chosen,
            {"type": "direct", "tag": "direct"},
        ],
        "route": {"rules": rules, "rule_set": rule_sets, "final": "select"},
        "experimental": {"clash_api": {"external_controller": settings["clash_api"], "secret": secret}},
    }


def check_config(path):
    r = subprocess.run([SING_BOX, "check", "-c", path], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()
