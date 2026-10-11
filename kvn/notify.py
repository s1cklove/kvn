import logging
import subprocess

from .settings import NOTIFY_SEND

log = logging.getLogger("kvn")


def notify(title, body="", urgency="normal", icon="network-vpn"):
    log.info("notify[%s]: %s | %s", urgency, title, body.replace("\n", " / "))
    try:
        subprocess.run([NOTIFY_SEND, "-a", "kvn", "-u", urgency, "-i", icon, title, body],
                       timeout=5, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("notify-send failed: %s", e)
