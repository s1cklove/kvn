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


def _chain_outbounds(settings, chain_servers):
    """Builds outbounds for the optional chain layer.

    Returns (outbounds_list, target_tag) where target_tag is either a single
    chain outbound tag or the 'chain' urltest group tag.
    """
    via = settings.get("chain_via", "chain")
    if via not in ("chain", "auto", "direct"):
        raise ValueError(f"invalid chain_via {via!r}; expected 'chain', 'auto' or 'direct'")

    nodes = []
    for s in chain_servers:
        if via == "chain":
            nodes.append({**s, "tag": f"chain:{s['tag']}", "detour": "select"})
        elif via == "direct":
            nodes.append({**s, "tag": f"chain:{s['tag']}"})
        else:
            nodes.append({**s, "tag": f"chain:{s['tag']} (direct)"})
            nodes.append({**s, "tag": f"chain:{s['tag']} (via sub)", "detour": "select"})

    tags = [n["tag"] for n in nodes]
    if len(nodes) == 1:
        return nodes, tags[0]
    group = {
        "type": "urltest", "tag": "chain", "outbounds": tags,
        "url": settings["test_url"], "interval": settings["urltest_interval"],
        "tolerance": settings["tolerance"], "interrupt_exist_connections": True,
    }
    return [group, *nodes], "chain"


def build_config(settings, servers, secret, chain_servers=None, chain_on=True):
    exclude = re.compile(settings["exclude"]) if settings["exclude"] else None
    chosen = [s for s in servers if not (exclude and exclude.search(s["tag"]))] or servers
    tags = [s["tag"] for s in chosen]

    rules, rule_sets = _route_rules(settings["bypass_russia"], settings["rulesets"])
    inbounds = [{"type": "mixed", "tag": "mixed-in",
                 "listen": settings["listen"], "listen_port": settings["port"]}]
    outbounds = [
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
    ]
    final = "select"

    if chain_servers:
        chain_obs, chain_target = _chain_outbounds(settings, chain_servers)
        out_selector = {
            "type": "selector", "tag": "out", "outbounds": ["select", chain_target],
            "default": chain_target if chain_on else "select",
            "interrupt_exist_connections": False,
        }
        outbounds = [out_selector, *chain_obs, *outbounds]
        if settings.get("chain_port"):
            inbounds.append({"type": "mixed", "tag": "chain-in",
                             "listen": settings["listen"], "listen_port": settings["chain_port"]})
            rules.append({"inbound": ["chain-in"], "outbound": "out"})
        else:
            final = "out"

    return {
        "log": {"level": "warn", "timestamp": False},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "route": {"rules": rules, "rule_set": rule_sets, "final": final},
        "experimental": {"clash_api": {"external_controller": settings["clash_api"], "secret": secret}},
    }


def check_config(path):
    r = subprocess.run([SING_BOX, "check", "-c", path], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()
