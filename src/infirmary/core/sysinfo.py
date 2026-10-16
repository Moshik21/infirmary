"""Read-only facts about this machine: its processes and its memory, from /proc or from `ps`/`vm_stat`.

Linux and WSL read a /proc tree (`doctor.proc_root`, which a test points at a synthetic tree); macOS runs
`ps`, `vm_stat` and `sysctl`. Anything an OS cannot give is None, never zero. A pid is never enough to
name a process (design rule 3.6), so every process record carries pid, start time and the full command.
"""
import calendar
import os
import re
import time

from infirmary.core import execute

PAGE = 4096


def _clk():
    try:
        return os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        return 100


def _read(path, limit=1 << 20):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit)
    except OSError:
        return None


def boot_epoch(root):
    raw = _read(os.path.join(root, "stat"))
    for row in (raw or b"").decode("utf-8", "replace").splitlines():
        if row.startswith("btime "):
            try:
                return int(row.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def _parse_stat(raw):
    """Fields after the LAST ')' (the command name may hold spaces and parentheses)."""
    close = raw.rfind(")")
    if close < 0:
        return None
    rest = raw[close + 2:].split()
    try:
        return {"state": rest[0], "ppid": int(rest[1]), "pgrp": int(rest[2]), "starttime": int(rest[19])}
    except (IndexError, ValueError):
        return None


def processes(root, osk, now=None, ps_run=None):
    """Every readable process: [{pid, ppid, uid, start (epoch), argv, cmdline, rss_kb, state}]."""
    if osk == "macos":
        return _ps_processes(ps_run or execute.run)
    out = []
    btime, clk = boot_epoch(root), float(_clk())
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        base = os.path.join(root, name)
        raw = _read(os.path.join(base, "stat"))
        if raw is None:
            continue
        st = _parse_stat(raw.decode("utf-8", "replace"))
        if st is None:
            continue
        cmd = _read(os.path.join(base, "cmdline")) or b""
        argv = [a.decode("utf-8", "replace") for a in cmd.split(b"\0") if a]
        try:
            uid = os.stat(base).st_uid
        except OSError:
            uid = None
        statm = (_read(os.path.join(base, "statm")) or b"").split()
        rss_kb = int(statm[1]) * PAGE // 1024 if len(statm) > 1 and statm[1].isdigit() else None
        out.append({"pid": int(name), "ppid": st["ppid"], "pgrp": st["pgrp"], "uid": uid, "state": st["state"],
                    "starttime": st["starttime"], "start": (btime + st["starttime"] / clk) if btime is not None else None,
                    "argv": argv, "cmdline": " ".join(argv), "rss_kb": rss_kb})
    return out


_PS_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\d{4})\s+(.*)$")


def _ps_processes(run):
    res = run(["ps", "-axo", "pid=,ppid=,uid=,rss=,lstart=,command="], timeout=5, env={"TZ": "UTC", "LC_ALL": "C"})
    out = []
    for row in res.out.splitlines():
        m = _PS_ROW.match(row)
        if not m:
            continue
        try:
            start = calendar.timegm(time.strptime(re.sub(r"\s+", " ", m.group(5)), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            start = None
        cmd = m.group(6).strip()
        out.append({"pid": int(m.group(1)), "ppid": int(m.group(2)), "pgrp": None, "uid": int(m.group(3)), "state": None,
                    "starttime": None, "start": start, "argv": cmd.split(" "), "cmdline": cmd, "rss_kb": int(m.group(4))})
    return out


def environ_keys(root, pid, keys):
    """Only the named variables of a same-user process, and nothing else is kept or shown."""
    raw = _read(os.path.join(root, str(pid), "environ"))
    if raw is None:
        return None
    want, out = set(keys), {}
    for item in raw.split(b"\0"):
        k, eq, v = item.partition(b"=")
        if eq and k.decode("utf-8", "replace") in want:
            out[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    return out


def environ_matching(root, pid, matches):
    """{name: value} for the variables of a same-user process whose name `matches(name)` says yes; nothing else is kept.
    None when the environment cannot be read (another user's process, no /proc)."""
    raw = _read(os.path.join(root, str(pid), "environ"))
    if raw is None:
        return None
    out = {}
    for item in raw.split(b"\0"):
        k, eq, v = item.partition(b"=")
        name = k.decode("utf-8", "replace")
        if eq and matches(name):
            out[name] = v.decode("utf-8", "replace")
    return out


def cwd(root, pid):
    try:
        return os.readlink(os.path.join(root, str(pid), "cwd"))
    except OSError:
        return None


def open_paths(root, pid, limit=512):
    """What a same-user process has open, as the paths its descriptors point at (links read, nothing opened); None when unreadable."""
    base = os.path.join(root, str(pid), "fd")
    try:
        names = os.listdir(base)
    except OSError:
        return None
    out = []
    for n in names[:limit]:
        try:
            out.append(os.readlink(os.path.join(base, n)))
        except OSError:
            continue
    return out


# ---- memory --------------------------------------------------------------------------------------
def _kv_kb(raw):
    out = {}
    for row in (raw or b"").decode("utf-8", "replace").splitlines():
        k, _, v = row.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k.strip()] = int(parts[0])
    return out


def _psi(root, name):
    raw = _read(os.path.join(root, "pressure", name))
    out = {}
    for row in (raw or b"").decode("utf-8", "replace").splitlines():
        parts = row.split()
        if not parts:
            continue
        vals = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        try:
            out[parts[0]] = {"avg10": float(vals["avg10"]), "avg60": float(vals["avg60"]), "avg300": float(vals["avg300"])}
        except (KeyError, ValueError):
            pass
    return out or None


def memory(root, osk, run=None):
    """total/available/swap in kB and percentages, plus kernel stall percentages where the OS has them."""
    if osk == "macos":
        return _mac_memory(run or execute.run)
    mi = _kv_kb(_read(os.path.join(root, "meminfo")))
    if "MemTotal" not in mi:
        return None
    total, avail = mi["MemTotal"], mi.get("MemAvailable", mi.get("MemFree"))
    swap_t, swap_f = mi.get("SwapTotal"), mi.get("SwapFree")
    psi = _psi(root, "memory") or {}
    return {"source": "proc", "total_kb": total, "avail_kb": avail,
            "avail_pct": round(100.0 * avail / total, 1) if avail is not None and total else None,
            "swap_total_kb": swap_t, "swap_used_kb": (swap_t - swap_f) if swap_t is not None and swap_f is not None else None,
            "swap_used_pct": round(100.0 * (swap_t - swap_f) / swap_t, 1) if swap_t and swap_f is not None else (0.0 if swap_t == 0 else None),
            "psi_full": (psi.get("full") or {}).get("avg60"), "psi_some": (psi.get("some") or {}).get("avg60"),
            "native_pressure": None}


def _mac_memory(run):
    vm = run(["vm_stat"], timeout=3)
    total = run(["sysctl", "-n", "hw.memsize"], timeout=3)
    swap = run(["sysctl", "-n", "vm.swapusage"], timeout=3)
    lvl = run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], timeout=3)
    if not vm.ok or not total.ok or not total.out.strip().isdigit():
        return None
    m = re.search(r"page size of (\d+) bytes", vm.out)
    page = int(m.group(1)) if m else 16384
    pages = {}
    for row in vm.out.splitlines():
        k, _, v = row.partition(":")
        v = v.strip().rstrip(".")
        if v.isdigit():
            pages[k.strip()] = int(v)
    total_kb = int(total.out.strip()) // 1024
    avail_pages = pages.get("Pages free", 0) + pages.get("Pages inactive", 0) + pages.get("Pages speculative", 0) + pages.get("Pages purgeable", 0)
    avail_kb = avail_pages * page // 1024
    sw_total = sw_used = None
    sm = re.search(r"total = ([\d.]+)([MG]).*?used = ([\d.]+)([MG])", swap.out) if swap.ok else None
    if sm:
        unit = lambda v, u: float(v) * (1024 * 1024 if u == "G" else 1024)
        sw_total, sw_used = int(unit(sm.group(1), sm.group(2))), int(unit(sm.group(3), sm.group(4)))
    level = {"1": "normal", "2": "warn", "4": "critical"}.get(lvl.out.strip()) if lvl.ok else None
    return {"source": "vm_stat", "total_kb": total_kb, "avail_kb": avail_kb, "avail_pct": round(100.0 * avail_kb / total_kb, 1) if total_kb else None,
            "swap_total_kb": sw_total, "swap_used_kb": sw_used,
            "swap_used_pct": round(100.0 * sw_used / sw_total, 1) if sw_total else (0.0 if sw_total == 0 else None),
            "psi_full": None, "psi_some": None, "native_pressure": level}
