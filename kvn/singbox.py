import os
import re
import subprocess

from .settings import RULES_DIR, SING_BOX


def build_config(settings, servers, secret):
    exclude = re.compile(settings["exclude"]) if settings["exclude"] else None
    chosen = [s for s in servers if not (exclude and exclude.search(s["tag"]))] or servers

    rules = [{"action": "sniff"}, {"ip_is_private": True, "outbound": "direct"}]
    rule_sets = []
    if settings["bypass_russia"]:
        rules.append({"domain_suffix": ["ru", "su", "xn--p1ai"], "outbound": "direct"})
        for tag in settings["rulesets"]:
            path = os.path.join(RULES_DIR, tag + ".srs")
            if os.path.exists(path):
                rule_sets.append({"type": "local", "tag": tag, "format": "binary", "path": path})
        if rule_sets:
            rules.append({"rule_set": [r["tag"] for r in rule_sets], "outbound": "direct"})

    tags = [s["tag"] for s in chosen]
    return {
        "log": {"level": "warn", "timestamp": False},
        "inbounds": [{"type": "mixed", "tag": "mixed-in",
                      "listen": settings["listen"], "listen_port": settings["port"]}],
        "outbounds": [
            # "select" is what routing actually uses; its default member is "auto"
            # (the urltest group), so out of the box traffic goes to the fastest live
            # server. `kvn use <server>` points it at one exit instead; `kvn use auto`
            # points it back. The daemon reads "select" to decide whether to intervene.
            {"type": "selector", "tag": "select", "outbounds": ["auto", *tags],
             "default": "auto", "interrupt_exist_connections": True},
            {"type": "urltest", "tag": "auto", "outbounds": tags,
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
