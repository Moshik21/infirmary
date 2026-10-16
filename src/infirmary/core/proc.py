"""Process identity and a cheap process-tree read.

A pid is never enough (design rule 3.6): a process is "the same process" only when pid, start time
and command line all match what was recorded. Linux and WSL read /proc; elsewhere the tree and the
CPU numbers are reported as unavailable (None) rather than guessed.
"""
import json
import os
import re
import time

from infirmary.core import execute

HAVE_PROC = os.path.isdir("/proc/self")


def _clk():
    try:
        return os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        return 100


def read_stat(pid):
    """Parse /proc/<pid>/stat after the LAST ')' (the command name may hold spaces and parentheses)."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    rest = raw[close + 2:].split()
    try:
        return {"state": rest[0], "ppid": int(rest[1]), "utime": int(rest[11]), "stime": int(rest[12]),
                "starttime": int(rest[19])}
    except (IndexError, ValueError):
        return None


def cmdline(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return None


def identity(pid):
    """What to record about a process so it can be recognised later. None when it is not there."""
    st = read_stat(pid)
    if st is None:
        return None
    return {"pid": pid, "starttime": st["starttime"], "cmdline": cmdline(pid)}


def same(recorded, pid=None):
    """True/False when the process can be judged, None when this platform cannot read it."""
    if not HAVE_PROC:
        return None
    pid = recorded.get("pid") if pid is None else pid
    now = identity(pid)
    if now is None:
        return False
    if recorded.get("starttime") is not None and now["starttime"] != recorded["starttime"]:
        return False
    if recorded.get("cmdline") and now["cmdline"] != recorded["cmdline"]:
        return False
    return True


def table():
    """pid -> {ppid, ticks} for every readable process; empty where /proc is absent."""
    out = {}
    if not HAVE_PROC:
        return out
    for name in os.listdir("/proc"):
        if name.isdigit():
            st = read_stat(int(name))
            if st:
                out[int(name)] = {"ppid": st["ppid"], "ticks": st["utime"] + st["stime"]}
    return out


def descendants(root, tab=None):
    tab = table() if tab is None else tab
    kids = {}
    for pid, row in tab.items():
        kids.setdefault(row["ppid"], []).append(pid)
    out, todo = [], [root]
    while todo:
        cur = todo.pop()
        for kid in kids.get(cur, []):
            out.append(kid)
            todo.append(kid)
    return out


def cpu_cores(root, sample_ms=400):
    """Cores burnt by `root` and everything under it over a short window. None where unreadable."""
    if not HAVE_PROC:
        return None
    first = table()
    if root not in first:
        return None
    tree = [root] + descendants(root, first)
    t0 = time.time()
    before = sum(first[p]["ticks"] for p in tree if p in first)
    time.sleep(max(0.05, sample_ms / 1000.0))
    second = table()
    after = sum(second[p]["ticks"] for p in tree if p in second)
    elapsed = time.time() - t0
    return max(0.0, (after - before) / float(_clk()) / elapsed) if elapsed > 0 else None


def boot_epoch():
    try:
        with open("/proc/stat") as fh:
            for row in fh:
                if row.startswith("btime "):
                    return int(row.split()[1])
    except (OSError, ValueError):
        pass
    res = execute.run(["sysctl", "-n", "kern.boottime"], timeout=2.0)   # macOS: "{ sec = 1790000000, usec = 0 } ..."
    m = re.search(r"sec\s*=\s*(\d+)", res.out) if res.ok else None
    return int(m.group(1)) if m else None


def boot_history(timeout=3.0):
    """Every boot this machine remembers, as epoch seconds, oldest first: the systemd journal's boot list
    (when `journalctl` is there and readable) plus this boot from the kernel. `[]` when neither speaks.
    Never guessed: a boot that neither source names is not in the list."""
    boots = []
    res = execute.run(["journalctl", "--list-boots", "-o", "json", "--no-pager"], timeout=timeout)
    if res.ok:
        try:
            for row in json.loads(res.out):
                first = row.get("first_entry")
                if isinstance(first, int) and first > 0:
                    boots.append(first // 1000000)   # microseconds; the first journal entry lands within seconds of the boot
        except (ValueError, AttributeError):
            pass
    now_boot = boot_epoch()
    if now_boot is not None:
        boots = [b for b in boots if b < now_boot - 60] + [now_boot]   # the kernel's own number replaces this boot's journal entry
    return sorted(set(boots))


def last_boot(boots, as_of=None):
    """The newest boot at or before `as_of` (now when None) from a `boot_history` list, or None. A moment older
    than every boot the list holds has no known boot, which is not the same as no boot."""
    cand = [b for b in boots if as_of is None or b <= as_of]
    return max(cand) if cand else None


def started_by(pid, epoch, slack_s=5):
    """True when `pid` exists and began no later than `epoch`: a recycled pid began after the job it is
    claimed to hold, so this is the identity check for records that did not store a start time."""
    st = read_stat(pid)
    btime = boot_epoch()
    if st is None:
        return False
    if btime is None:
        return None
    return btime + st["starttime"] / float(_clk()) <= epoch + slack_s
