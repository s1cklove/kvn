"""Defaults, user overrides and on-disk locations.

Files:
  $XDG_CONFIG_HOME/kvn/sub_url       subscription URL (secret, chmod 600)
  $XDG_CONFIG_HOME/kvn/chain_url     optional chain exit link(s) for `kvn chain` (secret, chmod 600)
  $XDG_CONFIG_HOME/kvn/config.json   optional overrides of DEFAULTS
  $XDG_STATE_HOME/kvn/               servers cache, generated config, rule-sets
"""

import json
import os
import secrets
import time

from .platform import chmod_secret, find_sing_box, mkdir_private, paths

DEFAULTS = {
    "listen": "127.0.0.1",
    "port": 10808,
    "clash_api": "127.0.0.1:9095",
    "api_listen": "127.0.0.1",
    "api_port": 9096,
    # Some panels hand out different formats per client; v2rayN gets plain links.
    "user_agent": "v2rayN/7.10",
    "sub_interval": 3 * 3600,
    "ruleset_interval": 24 * 3600,
    "check_interval": 30,
    "fail_threshold": 2,
    # After sing-box (re)starts urltest needs ~1.5 min to test everyone; don't cry wolf meanwhile.
    "startup_grace": 150,
    "recover_backoff": 120,
    # Whitelist exits drop Google/gstatic, so test against things that pass everywhere.
    "test_url": "https://1.1.1.1/cdn-cgi/trace",
    "probe_urls": ["https://api.anthropic.com/", "https://1.1.1.1/cdn-cgi/trace", "https://github.com/"],
    "direct_probe_urls": ["https://ya.ru", "https://vk.com"],
    # Servers whose name matches are left out of auto-selection (RU exit is useless).
    "exclude": "🇷🇺|Россия",
    "urltest_interval": "3m",
    "tolerance": 150,
    # Master server: regex; the fastest live match is used instead of urltest's pick
    # while it lags the leader by at most master_max_ratio times OR master_max_lag ms.
    # `kvn master off` turns it off without touching this config.
    "master": "Амстердам",
    "master_max_ratio": 2,
    "master_max_lag": 150,
    "notify_switch": True,
    "bypass_russia": True,
    "rulesets": {
        "geoip-ru": "https://testingcf.jsdelivr.net/gh/hiddify/hiddify-geo@rule-set/country/geoip-ru.srs",
        "geosite-ru": "https://testingcf.jsdelivr.net/gh/hiddify/hiddify-geo@rule-set/country/geosite-ru.srs",
        "geosite-category-ru": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@sing/geo/geosite/category-ru.srs",
        "geosite-ru-available-only-inside": "https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box/rule-set-geosite/geosite-ru-available-only-inside.srs",
    },
    "expire_warn_days": 3,
    # Optional chain exit layer (`kvn chain`, configured via ~/.config/kvn/chain_url):
    # chain_via: "chain" (detour through subscription), "auto" (urltest direct vs chained), or "direct".
    # chain_port: None toggles the main port on/off; an int (e.g. 10809) opens a dedicated port for the chain.
    "chain_via": "chain",
    "chain_port": None,
}
SING_BOX = find_sing_box()
NOTIFY_SEND = os.environ.get("KVN_NOTIFY_SEND", "notify-send")
CONF_DIR, STATE_DIR, RUN_DIR = paths()
SUB_URL_FILE = os.path.join(CONF_DIR, "sub_url")
CHAIN_URL_FILE = os.path.join(CONF_DIR, "chain_url")
SERVERS_FILE = os.path.join(STATE_DIR, "servers.json")
#: when / how the subscription was last fetched and when it expires (for `kvn status`)
SUB_META_FILE = os.path.join(STATE_DIR, "subscription.json")
BOX_CONFIG = os.path.join(STATE_DIR, "config.json")
SECRET_FILE = os.path.join(STATE_DIR, "clash_secret")
RULES_DIR = os.path.join(STATE_DIR, "rules")
#: exists while `kvn master off` is in effect
MASTER_OFF_FILE = os.path.join(STATE_DIR, "master_off")
#: exists while `kvn chain off` is in effect
CHAIN_OFF_FILE = os.path.join(STATE_DIR, "chain_off")
PID_FILE = os.path.join(RUN_DIR, "kvn.pid")
LOG_FILE = os.path.join(STATE_DIR, "kvn.log")


def load_settings():
    s = dict(DEFAULTS)
    path = os.path.join(CONF_DIR, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            s.update(json.load(f))
    return s


def write_atomic(path, data, mode=0o600):
    mkdir_private(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    chmod_secret(tmp, mode)
    os.replace(tmp, path)


def save_servers(servers, via, expire):
    write_atomic(SERVERS_FILE, json.dumps(servers, ensure_ascii=False))
    write_atomic(SUB_META_FILE, json.dumps({"fetched_at": int(time.time()), "via": via, "expire": expire, "count": len(servers)}))


def load_sub_meta():
    try:
        with open(SUB_META_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_secret():
    if not os.path.exists(SECRET_FILE):
        write_atomic(SECRET_FILE, secrets.token_hex(16))
    with open(SECRET_FILE, encoding="utf-8") as f:
        return f.read().strip()
