import logging
import subprocess
import sys

from .platform import popen_kwargs
from .settings import NOTIFY_SEND

log = logging.getLogger("kvn")

#: Shown as the sender of every desktop notification.
APP_NAME = "KVN Connect"


def _escape_applescript(text):
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _escape_ps_xml(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def notify(title, body="", urgency="normal", icon="network-vpn"):
    log.info("notify[%s]: %s | %s", urgency, title, body.replace("\n", " / "))
    try:
        if sys.platform == "darwin":
            script = f'display notification "{_escape_applescript(body)}" with title "{APP_NAME}" subtitle "{_escape_applescript(title)}"'
            subprocess.run(["osascript", "-e", script], timeout=5, check=False, capture_output=True)
        elif sys.platform == "win32":
            t_xml = _escape_ps_xml(f"{APP_NAME}: {title}")
            b_xml = _escape_ps_xml(body)
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                "$xml = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]::new(); "
                f"$xml.LoadXml('<toast><visual><binding template=\"ToastText02\"><text id=\"1\">{t_xml}</text><text id=\"2\">{b_xml}</text></binding></visual></toast>'); "
                f"$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
                f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_NAME}').Show($toast)"
            )
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                           timeout=5, check=False, capture_output=True, **popen_kwargs())
        else:
            subprocess.run([NOTIFY_SEND, "-a", APP_NAME, "-u", urgency, "-i", icon, title, body],
                           timeout=5, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("desktop notification failed: %s", e)
