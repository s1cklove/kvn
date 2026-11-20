"""Cross-platform OS helpers (Linux, macOS, Windows) using Python stdlib only."""

import ctypes
import ntpath
import os
import posixpath
import shutil
import subprocess
import sys
import time

SERVICE_NAME = "kvn"
LAUNCHD_LABEL = "io.github.s1cklove.kvn"
WIN_TASK_NAME = "KVN Connect"


def paths(platform=None, env=None, home=None):
    """Returns (conf_dir, state_dir, run_dir) for the given platform.

    conf_dir and state_dir are always distinct so user config.json never
    collides with the generated sing-box config.json.
    """
    plat = platform or sys.platform
    e = os.environ if env is None else env
    h = home or os.path.expanduser("~")

    if plat == "win32":
        appdata = e.get("APPDATA") or ntpath.join(h, "AppData", "Roaming")
        local = e.get("LOCALAPPDATA") or ntpath.join(h, "AppData", "Local")
        conf = ntpath.join(appdata, "kvn")
        state = ntpath.join(local, "kvn")
        return conf, state, state

    if plat == "darwin":
        conf_root = e.get("XDG_CONFIG_HOME")
        state_root = e.get("XDG_STATE_HOME")
        conf = posixpath.join(conf_root, "kvn") if conf_root else posixpath.join(h, "Library", "Application Support", "kvn")
        state = posixpath.join(state_root, "kvn") if state_root else posixpath.join(h, "Library", "Application Support", "kvn", "state")
        return conf, state, state

    # Linux / other POSIX (XDG)
    conf = posixpath.join(e.get("XDG_CONFIG_HOME") or posixpath.join(h, ".config"), "kvn")
    state = posixpath.join(e.get("XDG_STATE_HOME") or posixpath.join(h, ".local", "state"), "kvn")
    run = e.get("XDG_RUNTIME_DIR") or state
    return conf, state, run


def mkdir_private(path):
    if sys.platform == "win32":
        os.makedirs(path, exist_ok=True)
    else:
        os.makedirs(path, mode=0o700, exist_ok=True)


def chmod_secret(path, mode=0o600):
    if sys.platform != "win32":
        os.chmod(path, mode)


def is_pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def popen_kwargs(detached=False):
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if detached:
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True} if detached else {}


def find_sing_box(env=None):
    e = os.environ if env is None else env
    override = e.get("KVN_SING_BOX")
    if override:
        return override

    exe_name = "sing-box.exe" if sys.platform == "win32" else "sing-box"
    # Check bundled binary sitting next to the kvn executable, PyInstaller bundle, or python binary
    bases = [os.path.dirname(os.path.abspath(sys.argv[0])), os.path.dirname(os.path.abspath(sys.executable))]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.insert(0, meipass)
    for base in bases:
        cand = os.path.join(base, exe_name)
        if os.path.isfile(cand):
            return cand

    found = shutil.which("sing-box")
    if found:
        return found

    candidates = []
    if sys.platform == "darwin":
        candidates = ["/opt/homebrew/bin/sing-box", "/usr/local/bin/sing-box"]
    elif sys.platform == "win32":
        local = e.get("LOCALAPPDATA", "")
        profile = e.get("USERPROFILE", "")
        candidates = [
            os.path.join(local, "Microsoft", "WinGet", "Links", "sing-box.exe"),
            os.path.join(profile, "scoop", "shims", "sing-box.exe"),
        ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    return "sing-box"


def kvn_exec_argv():
    """Command vector to invoke `kvn` reliably from a background service."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = shutil.which("kvn")
    if exe:
        return [exe]
    py = sys.executable
    if sys.platform == "win32":
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.isfile(pyw):
            py = pyw
    return [py, "-m", "kvn"]


def running_under_systemd():
    return bool(os.environ.get("INVOCATION_ID"))


def is_config_externally_managed(conf_path):
    """True if config.json is a read-only symlink (e.g. NixOS Home Manager)."""
    if not os.path.exists(conf_path):
        return False
    if os.path.islink(conf_path) and os.path.realpath(conf_path).startswith("/nix/store/"):
        return True
    return not os.access(conf_path, os.W_OK)


# ---------------------------------------------------------------- service management

def _systemd_unit_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "systemd", "user", f"{SERVICE_NAME}.service")


def _launchd_plist_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")


def _systemd_installed():
    return sys.platform.startswith("linux") and bool(shutil.which("systemctl"))


def _systemd_has_unit():
    return _systemd_installed() and (
        os.path.exists(_systemd_unit_path())
        or os.path.exists(f"/etc/systemd/user/{SERVICE_NAME}.service")
    )


def service_install(log_file):
    """Registers kvn to start automatically on user login. Returns status message."""
    argv = [*kvn_exec_argv(), "run"]

    if sys.platform.startswith("linux"):
        if not shutil.which("systemctl"):
            raise OSError("systemctl not found; run `kvn run` under your own supervisor")
        unit = _systemd_unit_path()
        if os.path.islink(unit) and os.path.realpath(unit).startswith("/nix/store/"):
            return f"already managed by NixOS / Home Manager ({unit})"
        os.makedirs(os.path.dirname(unit), exist_ok=True)
        exec_line = " ".join(argv)
        content = (
            "[Unit]\n"
            "Description=kvn: sing-box proxy with auto failover\n"
            "Wants=network-online.target\n"
            "After=network-online.target\n"
            "\n"
            "[Service]\n"
            f"ExecStart={exec_line}\n"
            "Restart=always\n"
            "RestartSec=10\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        with open(unit, "w", encoding="utf-8") as f:
            f.write(content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", SERVICE_NAME], check=True)
        return f"installed and enabled systemd user unit: {unit}"

    if sys.platform == "darwin":
        import plistlib
        plist = _launchd_plist_path()
        os.makedirs(os.path.dirname(plist), exist_ok=True)
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        data = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": argv,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": log_file,
            "StandardErrorPath": log_file,
            "EnvironmentVariables": {
                "PYTHONUNBUFFERED": "1",
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            },
        }
        with open(plist, "wb") as f:
            plistlib.dump(data, f)
        return f"installed LaunchAgent: {plist} (start with: kvn start)"

    if sys.platform == "win32":
        cmd = subprocess.list2cmdline(argv)
        r = subprocess.run(
            ["schtasks", "/Create", "/TN", WIN_TASK_NAME, "/TR", cmd, "/SC", "ONLOGON", "/F"],
            capture_output=True, text=True, **popen_kwargs(),
        )
        if r.returncode != 0:
            raise OSError((r.stderr or r.stdout).strip() or "schtasks failed")
        return f"installed logon task '{WIN_TASK_NAME}' in Windows Task Scheduler"

    raise OSError(f"autostart install is not supported on {sys.platform}")


def service_uninstall():
    if sys.platform.startswith("linux"):
        unit = _systemd_unit_path()
        if os.path.islink(unit) and os.path.realpath(unit).startswith("/nix/store/"):
            return f"managed by NixOS / Home Manager ({unit}); disable services.kvn.enable in Nix config"
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
        if os.path.exists(unit):
            os.unlink(unit)
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return f"removed systemd user unit: {unit}"

    if sys.platform == "darwin":
        plist = _launchd_plist_path()
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LAUNCHD_LABEL}"], check=False, capture_output=True)
        if os.path.exists(plist):
            os.unlink(plist)
        return f"removed LaunchAgent: {plist}"

    if sys.platform == "win32":
        subprocess.run(["schtasks", "/Delete", "/TN", WIN_TASK_NAME, "/F"],
                       check=False, capture_output=True, **popen_kwargs())
        return f"removed logon task '{WIN_TASK_NAME}'"

    raise OSError(f"autostart uninstall is not supported on {sys.platform}")


def service_control(action, stop_fn=None, pid_fn=None):
    """Runs start/stop/restart on the current OS. Returns True on success."""
    if _systemd_has_unit():
        r = subprocess.run(["systemctl", "--user", action, SERVICE_NAME], check=False)
        if r.returncode != 0:
            raise OSError(f"systemctl --user {action} {SERVICE_NAME} failed")
        return True

    if sys.platform == "darwin" and os.path.exists(_launchd_plist_path()):
        uid = os.getuid()
        plist = _launchd_plist_path()
        target = f"gui/{uid}/{LAUNCHD_LABEL}"
        if action in ("stop", "restart"):
            subprocess.run(["launchctl", "bootout", target], check=False, capture_output=True)
        if action in ("start", "restart"):
            r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist], check=False, capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(["launchctl", "kickstart", "-k", target], check=True)
        return True

    # Detached background process fallback (Windows, or macOS/Linux without installed service)
    if action in ("stop", "restart"):
        if stop_fn:
            stop_fn()
        if pid_fn:
            for _ in range(30):
                if not pid_fn():
                    break
                time.sleep(0.2)
    if action in ("start", "restart"):
        if pid_fn and pid_fn():
            return True
        argv = [*kvn_exec_argv(), "run"]
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **popen_kwargs(detached=True))
    return True


def tail_file(path, lines=50, follow=True):
    if not os.path.exists(path):
        raise FileNotFoundError(f"no log file yet at {path}")
    with open(path, encoding="utf-8", errors="replace") as f:
        buf = f.readlines()
        for line in buf[-lines:]:
            sys.stdout.write(line)
        sys.stdout.flush()
        if not follow:
            return 0
        try:
            while True:
                line = f.readline()
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                else:
                    time.sleep(0.3)
        except KeyboardInterrupt:
            return 0


def show_logs(log_file, lines=50, follow=True):
    if _systemd_installed() and os.path.exists(_systemd_unit_path()) and shutil.which("journalctl"):
        cmd = ["journalctl", "--user", "-u", SERVICE_NAME, "-n", str(lines)]
        if follow:
            cmd.append("-f")
        try:
            return subprocess.run(cmd).returncode
        except KeyboardInterrupt:
            return 0
    return tail_file(log_file, lines=lines, follow=follow)
