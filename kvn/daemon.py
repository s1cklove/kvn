import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time

from .clash import Clash, top_list
from .net import probe
from .notify import notify
from .settings import BOX_CONFIG, PID_FILE, RULES_DIR, SERVERS_FILE, SING_BOX, STATE_DIR, get_secret, write_atomic
from .singbox import build_config, check_config
from .net import http_get
from .subscription import fetch_subscription, servers_digest, subscription_expiry

log = logging.getLogger("kvn")


class Watchdog:
    def __init__(self, settings):
        self.s = settings
        self.proxy = f"http://{settings['listen']}:{settings['port']}"
        self.clash = Clash(settings["clash_api"], get_secret())
        self.box = None
        self.servers = []
        self.state = "init"          # init | up | down | offline
        self.fails = 0
        self.current = None
        self.down_since = 0
        self.last_recover = 0
        self.last_dead_notice = 0
        self.next_sub = 0
        self.next_rules = 0
        self.expire_warned_day = None
        self.box_error_notified = False
        self.grace_until = 0
        self.wake = threading.Event()
        self.stop = False
        self.force_update = False

    # -- sing-box process

    def port_free(self):
        try:
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.s["listen"], self.s["port"]))
            return True
        except OSError:
            return False

    def start_box(self):
        if not self.port_free():
            if not self.box_error_notified:
                notify("VPN не запущен", f"Порт {self.s['port']} занят — запущен другой агрегатор VPN.",
                       "critical", "network-vpn-disconnected")
                self.box_error_notified = True
            return False
        self.box = subprocess.Popen([SING_BOX, "run", "--disable-color", "-c", BOX_CONFIG, "-D", STATE_DIR])
        for _ in range(50):
            if self.box.poll() is not None:
                break
            try:
                self.clash.get("/version", timeout=1)
                self.box_error_notified = False
                self.grace_until = time.time() + self.s["startup_grace"]
                log.info("sing-box started (pid %d)", self.box.pid)
                return True
            except OSError:
                time.sleep(0.2)
        log.error("sing-box did not come up")
        self.stop_box()
        return False

    def stop_box(self):
        if self.box and self.box.poll() is None:
            self.box.terminate()
            try:
                self.box.wait(5)
            except subprocess.TimeoutExpired:
                self.box.kill()
        self.box = None

    def apply_config(self):
        """Writes config for current servers; restarts sing-box only if config changed."""
        cfg = json.dumps(build_config(self.s, self.servers, self.clash.secret), ensure_ascii=False, indent=1)
        old = None
        if os.path.exists(BOX_CONFIG):
            with open(BOX_CONFIG) as f:
                old = f.read()
        if cfg == old and self.box and self.box.poll() is None:
            return True
        tmp = BOX_CONFIG + ".new"
        write_atomic(tmp, cfg)
        ok, err = check_config(tmp)
        if not ok:
            os.unlink(tmp)
            log.error("generated config rejected by sing-box: %s", err)
            notify("Конфиг не прошёл проверку", err[:300], "critical", "dialog-error")
            return False
        os.replace(tmp, BOX_CONFIG)
        self.stop_box()
        return self.start_box()

    # -- subscription / rule-sets

    def load_cached_servers(self):
        if os.path.exists(SERVERS_FILE):
            with open(SERVERS_FILE) as f:
                self.servers = json.load(f)
            log.info("loaded %d cached servers", len(self.servers))

    def refresh_subscription(self, take_rotation=False):
        """Fetches the subscription; returns True if the server list changed.

        Rotated params alone (SNI/short_id/path change on every fetch) only count when
        take_rotation is set, i.e. while recovering, so routine refreshes don't restart sing-box.
        """
        # Blanc refuses the subscription from its own exits, so direct goes first.
        try:
            servers, skipped, headers, via = fetch_subscription(self.s, [None, self.proxy])
        except OSError as e:
            log.warning("subscription fetch failed: %s", e)
            return False
        if skipped:
            log.info("skipped entries: %s", skipped)
        self.check_expiry(headers)
        if self.servers and servers_digest(servers, take_rotation) == servers_digest(self.servers, take_rotation):
            log.info("subscription unchanged (%d servers, via %s)", len(servers), via)
            return False
        old_tags = {s["tag"] for s in self.servers}
        new_tags = {s["tag"] for s in servers}
        added, removed = new_tags - old_tags, old_tags - new_tags
        self.servers = servers
        write_atomic(SERVERS_FILE, json.dumps(servers, ensure_ascii=False))
        log.info("subscription updated via %s: %d servers (+%d -%d)", via, len(servers), len(added), len(removed))
        if old_tags and (added or removed):
            notify("Подписка обновилась", f"{len(servers)} серверов: +{len(added)} / −{len(removed)}", "low")
        return True

    def check_expiry(self, headers):
        exp = subscription_expiry(headers)
        if not exp:
            return
        left = (exp - time.time()) / 86400
        today = time.strftime("%F")
        if left < self.s["expire_warn_days"] and self.expire_warned_day != today:
            self.expire_warned_day = today
            notify("Подписка BlancVPN скоро кончится",
                   f"Осталось {max(left, 0):.1f} дн. (до {time.strftime('%d.%m %H:%M', time.localtime(exp))})",
                   "critical", "dialog-warning")

    def refresh_rulesets(self):
        """Downloads rule-sets; returns True if a new one became available."""
        os.makedirs(RULES_DIR, exist_ok=True)
        appeared = False
        for tag, url in self.s["rulesets"].items():
            path = os.path.join(RULES_DIR, tag + ".srs")
            for proxy in (self.proxy, None):
                try:
                    status, _, body = http_get(url, proxy=proxy, timeout=60)
                    if status == 200 and body[:3] == b"SRS":
                        appeared |= not os.path.exists(path)
                        write_atomic(path, body, 0o644)
                        break
                    log.warning("rule-set %s: HTTP %s / bad magic", tag, status)
                except OSError as e:
                    log.warning("rule-set %s via %s: %s", tag, "proxy" if proxy else "direct", e)
        return appeared

    # -- health

    def describe(self, tag):
        if not tag:
            return "ещё выбирается"
        try:
            d = self.clash.last_delay(tag)
            return f"{tag} — {d} мс" if d else tag
        except OSError:
            return tag

    def handle_up(self, extra=""):
        self.fails = 0
        prev = self.state
        self.state = "up"
        try:
            now = self.clash.current() or None
        except OSError:
            now = None
        if prev == "init" and not now:
            self.state = "init"      # urltest hasn't picked yet; announce once it has
            return
        if prev == "down":
            mins = (time.time() - self.down_since) / 60
            notify("Живём", f"Сервер: {self.describe(now)}\nЛежало {mins:.0f} мин{extra}", "normal", "network-vpn")
        elif prev == "init":
            notify("VPN поднят", f"Сервер: {self.describe(now)}", "low", "network-vpn")
        elif now and self.current and now != self.current and self.s["notify_switch"]:
            notify("Сменил сервер", f"{self.current} → {self.describe(now)}", "low", "network-vpn")
        self.current = now

    def recover(self):
        self.last_recover = time.time()
        if self.refresh_subscription(take_rotation=True):
            self.apply_config()
        try:
            alive = self.clash.test_all(self.s["test_url"])
        except OSError as e:
            log.warning("group test failed: %s", e)
            alive = {}
        total = len(self.group_members())
        log.info("group test: %d/%d alive; best: %s", len(alive), total, top_list(alive, 5))
        if not alive:
            if time.time() - self.last_dead_notice > 900:
                self.last_dead_notice = time.time()
                notify("Все серверы лежат", f"0 из {total} отвечают. Пробую снова каждые "
                       f"{self.s['recover_backoff'] // 60} мин.", "critical", "network-vpn-disconnected")
            return
        if probe(self.s["probe_urls"], self.proxy):
            self.handle_up(f"\nЖивых {len(alive)}/{total}, лучшие: {top_list(alive)}")

    def group_members(self):
        try:
            return self.clash.get("/proxies/auto").get("all", [])
        except OSError:
            return []

    def tick(self):
        if probe(self.s["probe_urls"], self.proxy):
            self.handle_up()
            return
        if time.time() < self.grace_until:
            log.info("proxy probe failed during startup grace, urltest is still picking")
            return
        self.fails += 1
        log.info("proxy probe failed (%d/%d)", self.fails, self.s["fail_threshold"])
        if self.fails < self.s["fail_threshold"]:
            return
        if not probe(self.s["direct_probe_urls"]):
            if self.state != "offline":
                log.warning("no direct internet either, waiting for network")
                self.state = "offline"
            return
        if self.state != "down":
            what = f"VPN отвалился (сервер: {self.current})" if self.current else "VPN не поднимается"
            self.state = "down"
            self.down_since = time.time()
            notify("Пришла пизда!", f"{what}. Обновляю подписку и ищу живой сервер…",
                   "critical", "network-vpn-disconnected")
            self.recover()
        elif time.time() - self.last_recover >= self.s["recover_backoff"]:
            self.recover()

    # -- main loop

    def run(self):
        try:
            self._run()
        finally:
            self.stop_box()

    def _run(self):
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        write_atomic(PID_FILE, str(os.getpid()), 0o644)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self.on_stop)
        signal.signal(signal.SIGUSR1, self.on_usr1)

        self.load_cached_servers()
        while not self.stop and not self.servers:
            self.refresh_subscription()
            if not self.servers:
                notify("Не могу скачать подписку", "Серверов в кэше нет, пробую снова через минуту",
                       "critical", "network-vpn-disconnected")
                self.wake.wait(60)
        self.next_sub = time.time() + self.s["sub_interval"]
        if not self.stop:
            self.apply_config()

        while not self.stop:
            if self.box is None or self.box.poll() is not None:
                if self.box is not None:
                    log.error("sing-box exited with %s, restarting", self.box.returncode)
                    notify("sing-box упал", "Перезапускаю", "normal", "dialog-warning")
                if not self.start_box():
                    self.wake.wait(60)
                    self.wake.clear()
                    continue

            now = time.time()
            if self.force_update:
                self.force_update = False
                log.info("manual update requested")
                self.next_sub = now + self.s["sub_interval"]
                self.recover()
            elif now >= self.next_sub:
                self.next_sub = now + self.s["sub_interval"]
                if self.refresh_subscription():
                    self.apply_config()

            try:
                self.tick()
                if self.state == "up" and now >= self.next_rules:
                    self.next_rules = now + self.s["ruleset_interval"]
                    if self.refresh_rulesets():
                        self.apply_config()
            except Exception:
                log.exception("tick failed, carrying on")

            self.wake.wait(self.s["check_interval"])
            self.wake.clear()

        self.stop_box()
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass

    def on_stop(self, *_):
        self.stop = True
        self.wake.set()

    def on_usr1(self, *_):
        self.force_update = True
        self.wake.set()
