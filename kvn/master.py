"""Master server: a preferred exit used instead of urltest's pick while it is good enough.

"Good enough" = it answers and lags the leader (the fastest live server) by at most
master_max_ratio times OR at most master_max_lag ms. Deliberately crude: it will
sometimes keep a slower master, which is the point.
"""

import os
import re

from .settings import MASTER_OFF_FILE, write_atomic


def enabled(settings):
    return bool(settings["master"]) and not os.path.exists(MASTER_OFF_FILE)


def set_enabled(on):
    if on:
        try:
            os.unlink(MASTER_OFF_FILE)
        except FileNotFoundError:
            pass
    else:
        write_atomic(MASTER_OFF_FILE, "")


def evaluate(settings, delays):
    """Returns (master tag or None, its delay, leader delay, whether it's good enough)."""
    alive = {t: d for t, d in delays.items() if d > 0}
    rx = re.compile(settings["master"]) if settings["master"] else None
    mine = sorted((d, t) for t, d in alive.items() if rx and rx.search(t))
    if not mine:
        return None, 0, 0, False
    delay, tag = mine[0]
    best = min(alive.values())
    ok = delay <= best * settings["master_max_ratio"] or delay - best <= settings["master_max_lag"]
    return tag, delay, best, ok


def choose(settings, delays):
    """What the "auto" selector should point at: the master server or "fastest"."""
    if not enabled(settings):
        return "fastest"
    tag, _, _, ok = evaluate(settings, delays)
    return tag if ok else "fastest"


def steer(settings, clash):
    """Points "auto" where choose() says. Returns (before, after); raises OSError."""
    _, delays = clash.delays()
    before, want = clash.auto_selected(), choose(settings, delays)
    if want != before:
        clash.select_auto(want)
    return before, want
