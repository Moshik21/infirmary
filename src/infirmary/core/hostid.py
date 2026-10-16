"""Host identity: who is this machine, and which boot is it. Config can name it; nothing is invented."""
import os
import platform
import socket
import sys


def os_kind():
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    try:
        with open("/proc/version", "r") as fh:
            if "microsoft" in fh.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            return fh.read().strip()
    except OSError:
        return None


def identity(cfg=None):
    name = ((cfg or {}).get("host") or {}).get("id") if isinstance((cfg or {}).get("host"), dict) else None
    return {
        "id": name or socket.gethostname().split(".")[0].lower(),
        "os": os_kind(),
        "python": platform.python_version(),
        "boot_id": boot_id(),
    }
