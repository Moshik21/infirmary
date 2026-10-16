"""Read a `lookout.beat` file (written by lookout-beat) without sampling anything ourselves.

The age is the FILE's age on this machine's own clock, never a comparison of two machines' clocks:
that is what tells a beat that stopped from one that is merely slow.
"""
import json
import os
import sys
import time


def default_path():
    """Where lookout-beat writes its beat on this OS (docs/beat.md of Lookout)."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/lookout-beat/beat.json")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "lookout-beat", "beat.json")
    return os.path.expanduser("~/.cache/lookout-beat/beat.json")


def read(path, now=None):
    now = time.time() if now is None else now
    try:
        st = os.stat(path)
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    machine = doc.get("machine") or {}
    pressure = machine.get("pressure") or {}
    return {
        "host": doc.get("host"),
        "level": pressure.get("level") or "unknown",
        "why": pressure.get("why") or [],
        "age_s": max(0, int(now - st.st_mtime)),
        "interval_s": doc.get("interval_s"),
        "seq": doc.get("seq"),
        "mem": machine.get("mem") or {},
        "disks": machine.get("disks") or [],
    }
