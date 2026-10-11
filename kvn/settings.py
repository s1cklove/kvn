"""Defaults, user overrides and on-disk locations.

Files:
  $XDG_CONFIG_HOME/kvn/sub_url       subscription URL (secret, chmod 600)
  $XDG_CONFIG_HOME/kvn/config.json   optional overrides of DEFAULTS
  $XDG_STATE_HOME/kvn/               servers cache, generated config, rule-sets
"""

import json
import os
import secrets

DEFAULTS = {
    "listen": "127.0.0.1",
    "port": 10808,
    "clash_api": "127.0.0.1:9095",
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
    "notify_switch": True,
    "bypass_russia": True,
    "rulesets": {
        "geoip-ru": "https://testingcf.jsdelivr.net/gh/hiddify/hiddify-geo@rule-set/country/geoip-ru.srs",
        "geosite-ru": "https://testingcf.jsdelivr.net/gh/hiddify/hiddify-geo@rule-set/country/geosite-ru.srs",
        "geosite-category-ru": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@sing/geo/geosite/category-ru.srs",
        "geosite-ru-available-only-inside": "https://testingcf.jsdelivr.net/gh/runetfreedom/russia-v2ray-rules-dat@release/sing-box/rule-set-geosite/geosite-ru-available-only-inside.srs",
    },
    "expire_warn_days": 3,
}
SING_BOX = os.environ.get("KVN_SING_BOX", "sing-box")
NOTIFY_SEND = os.environ.get("KVN_NOTIFY_SEND", "notify-send")
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "kvn")
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "kvn")
RUN_DIR = os.environ.get("XDG_RUNTIME_DIR", STATE_DIR)
SUB_URL_FILE = os.path.join(CONF_DIR, "sub_url")
SERVERS_FILE = os.path.join(STATE_DIR, "servers.json")
BOX_CONFIG = os.path.join(STATE_DIR, "config.json")
SECRET_FILE = os.path.join(STATE_DIR, "clash_secret")
RULES_DIR = os.path.join(STATE_DIR, "rules")
PID_FILE = os.path.join(RUN_DIR, "kvn.pid")


def load_settings():
    s = dict(DEFAULTS)
    path = os.path.join(CONF_DIR, "config.json")
    if os.path.exists(path):
        with open(path) as f:
            s.update(json.load(f))
    return s


def write_atomic(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def get_secret():
    if not os.path.exists(SECRET_FILE):
        write_atomic(SECRET_FILE, secrets.token_hex(16))
    with open(SECRET_FILE) as f:
        return f.read().strip()
