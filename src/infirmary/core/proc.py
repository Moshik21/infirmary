"""Process identity and a cheap process-tree read.

A pid is never enough (design rule 3.6): a process is "the same process" only when pid, start time
and command line all match what was recorded. Linux and WSL read /proc; macOS reads `ps` and Windows reads
Win32_Process through PowerShell, both read-only from a fixed argv. Where none of these can be read (no reader for
the platform, or its tool is missing, times out or prints anything it cannot parse whole) the identity, the tree and
the CPU numbers are reported as unavailable (None), and `why` says which, rather than guessed.
"""
import base64
import calendar
import json
import os
import re
import sys
import time

from infirmary.core import execute

HAVE_PROC = os.path.isdir("/proc/self")
MACOS = sys.platform == "darwin"
WINDOWS = os.name == "nt"
why = None   # why the last macOS or Windows read failed; None after one that worked, and always on Linux


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
    if not HAVE_PROC:
        got = _read(pid)
        row = got["samples"][0].get(pid) if got else None
        return {"pid": pid, "starttime": row["starttime"], "cmdline": got["cmdline"]} if row else None
    st = read_stat(pid)
    if st is None:
        return None
    return {"pid": pid, "starttime": st["starttime"], "cmdline": cmdline(pid)}


def same(recorded, pid=None):
    """True/False when the process can be judged, None when this platform cannot read it."""
    pid = recorded.get("pid") if pid is None else pid
    if not HAVE_PROC:
        return _judge(_read(pid if recorded.get("cmdline") else None), recorded, pid)
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
        got = _read()
        return {pid: {"ppid": row["ppid"], "ticks": row["ticks"]} for pid, row in got["samples"][0].items()} if got else out
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
    out, todo, seen = [], [root], {root}
    while todo:
        cur = todo.pop()
        for kid in kids.get(cur, []):
            if kid not in seen:     # a Windows parent id can loop back (pid 0 is its own parent)
                seen.add(kid)
                out.append(kid)
                todo.append(kid)
    return out


def cpu_cores(root, sample_ms=400):
    """Cores burnt by `root` and everything under it over a short window. None where unreadable."""
    if not HAVE_PROC:
        return _cores(_read(sample_ms=max(50, sample_ms)), root)
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
    if not HAVE_PROC:
        got = _read()
        if got is None:
            return None
        row = got["samples"][0].get(pid)
        return False if row is None else row["start"] <= epoch + slack_s
    st = read_stat(pid)
    btime = boot_epoch()
    if st is None:
        return False
    if btime is None:
        return None
    return btime + st["starttime"] / float(_clk()) <= epoch + slack_s


# ---- macOS and Windows: the same answers from the platform's own process table ----------------------------------------
# A table row is {ppid, ticks, starttime, start}: ticks is CPU time (`hz` of them a second), starttime is the exact value an
# identity records (epoch seconds from `ps`, a FILETIME on Windows) and start is epoch seconds. The table is read without
# command lines (a busy machine's would pass execute.MAX_OUT); the one command line an identity needs is read on its own.

PS_TABLE = ["ps", "-axo", "pid=,ppid=,time=,lstart="]
PS_ENV = {"TZ": "UTC", "LC_ALL": "C"}     # lstart in UTC and in English, whatever the user's zone and locale
_PS_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+((?:\d+-)?\d+(?::\d+)*\.\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\d{4})\s*$")

# Read-only: one CIM query (two, `INFIRMARY_SAMPLE_MS` apart, for a CPU sample). The pid and the gap arrive as environment
# variables parsed as integers, so nothing is ever spliced into the script. `E count ms` closes each sample, so a cut-off
# answer never parses; the command line is base64 so no character in it can break a row.
WIN_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$want = 0; [void][int]::TryParse([string]$env:INFIRMARY_PID, [ref]$want)
$gap = 0; [void][int]::TryParse([string]$env:INFIRMARY_SAMPLE_MS, [ref]$gap)
$clock = [Diagnostics.Stopwatch]::StartNew()
foreach ($round in @(1, 2)) {
    if ($round -eq 2) { if ($gap -le 0) { break }; Start-Sleep -Milliseconds $gap }
    $all = @(Get-CimInstance -ClassName Win32_Process)
    foreach ($p in $all) {
        $born = '-'; if ($p.CreationDate) { $born = $p.CreationDate.ToFileTimeUtc() }
        'P {0} {1} {2} {3}' -f $p.ProcessId, $p.ParentProcessId, $born, ([uint64]$p.KernelModeTime + [uint64]$p.UserModeTime)
    }
    'E {0} {1}' -f $all.Count, $clock.ElapsedMilliseconds
}
$me = @($all | Where-Object { $_.ProcessId -eq $want })
if ($want -gt 0 -and $me.Count -eq 1 -and $null -ne $me[0].CommandLine) { 'C ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($me[0].CommandLine)) }
"""
WIN_ARGV = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(WIN_SCRIPT.encode("utf-16-le")).decode("ascii")]
FILETIME_EPOCH = 11644473600     # seconds from 1601-01-01 to 1970-01-01


def _ps_ticks(text):
    """`ps` CPU time ([dd-][hh:]mm:ss.cc) in hundredths of a second."""
    days, _, rest = text.rpartition("-")
    whole, _, frac = rest.partition(".")
    secs = 0
    for part in whole.split(":"):
        secs = secs * 60 + int(part)
    return (int(days or 0) * 86400 + secs) * 100 + int((frac + "00")[:2])


def parse_ps(text):
    """`PS_TABLE` output (under PS_ENV) -> {pid: row}; None when any row does not parse, or there is none."""
    rows = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _PS_ROW.match(line)
        if not m:
            return None
        try:
            start = calendar.timegm(time.strptime(" ".join(m.group(4).split()), "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            return None
        rows[int(m.group(1))] = {"ppid": int(m.group(2)), "ticks": _ps_ticks(m.group(3)), "starttime": start, "start": start}
    return rows or None


def parse_cim(text):
    """`WIN_SCRIPT` output -> {samples: [{pid: row}, ...], elapsed (seconds between them, None for one), cmdline}; None when
    any line does not parse or a sample's count does not match its rows."""
    samples, marks, rows, cmd = [], [], {}, None
    for line in text.splitlines():
        f = line.split()
        if not f:
            continue
        try:
            if f[0] == "P" and len(f) == 5:
                born = None if f[3] == "-" else int(f[3])
                rows[int(f[1])] = {"ppid": int(f[2]), "ticks": int(f[4]), "starttime": born,
                                   "start": None if born is None else born / 1e7 - FILETIME_EPOCH}
            elif f[0] == "E" and len(f) == 3 and int(f[1]) == len(rows):
                for row in rows.values():   # Windows keeps a dead parent's id, and the id may be reused by a younger process
                    parent = rows.get(row["ppid"])
                    if parent is not None and (parent is row or None in (parent["starttime"], row["starttime"]) or parent["starttime"] > row["starttime"]):
                        row["ppid"] = None
                samples.append(rows)
                marks.append(int(f[2]))
                rows = {}
            elif f[0] == "C" and len(f) == 2 and samples:
                cmd = base64.b64decode(f[1], validate=True).decode("utf-8")
            else:
                return None
        except (ValueError, TypeError):     # binascii.Error and UnicodeDecodeError are ValueErrors
            return None
    if rows or not samples or len(samples) > 2:
        return None
    return {"samples": samples, "elapsed": (marks[1] - marks[0]) / 1000.0 if len(marks) == 2 else None, "cmdline": cmd}


def _tool(argv, timeout, env=None):
    """(stdout, None) from a reader that ran cleanly, else (None, why not)."""
    res = execute.run(argv, timeout=timeout, env=env)
    name = argv[0]
    if res.missing:
        return None, "%s not found" % name
    if res.timed_out:
        return None, "%s timed out after %gs" % (name, timeout)
    if res.rc != 0:
        return None, "%s exited %s" % (name, res.rc)
    if len(res.out) >= execute.MAX_OUT:
        return None, "%s printed more than the reader keeps" % name
    return res.out, None


def _read_ps(pid, sample_ms):
    samples, t0, elapsed = [], None, None
    for i in range(2 if sample_ms else 1):
        if i:
            time.sleep(sample_ms / 1000.0)
        out, problem = _tool(PS_TABLE, 3.0, PS_ENV)
        rows = parse_ps(out) if out is not None else None
        if rows is None:
            return None, problem or "ps printed a process table it could not parse"
        samples.append(rows)
        if i:
            elapsed = time.time() - t0
        t0 = time.time()
    cmd = None
    if pid is not None and pid in samples[-1]:
        out, _ = _tool(["ps", "-ww", "-p", str(int(pid)), "-o", "command="], 3.0, PS_ENV)
        cmd = out.strip() if out and out.strip() else None
    return {"samples": samples, "elapsed": elapsed, "cmdline": cmd, "hz": 100}, None


def _read_cim(pid, sample_ms):
    env = {"INFIRMARY_PID": str(int(pid)) if pid is not None else None, "INFIRMARY_SAMPLE_MS": str(int(sample_ms)) if sample_ms else None}
    out, problem = _tool(WIN_ARGV, 10.0 + (sample_ms or 0) / 1000.0, env)   # a cold WMI on a fresh machine can take seconds
    got = parse_cim(out) if out is not None else None
    if got is None:
        return None, problem or "powershell.exe printed a process table it could not parse"
    got["hz"] = 10 ** 7      # Win32_Process CPU times are in 100 ns units
    return got, None


def _read(pid=None, sample_ms=None):
    """This platform's process table (two samples `sample_ms` apart when asked) and `pid`'s command line; None, with `why`
    set, when it cannot be read whole."""
    global why
    if MACOS:
        got, why = _read_ps(pid, sample_ms)
    elif WINDOWS:
        got, why = _read_cim(pid, sample_ms)
    else:
        got, why = None, "no process reader for this platform"
    return got


def look(recorded, sample_ms=None):
    """Off Linux: `same()` and, with `sample_ms`, `cpu_cores()` and the descendant count, all from ONE read, as {alive,
    cpu_cores, descendants}. Each Windows read starts PowerShell (about half a second), so three would not fit a verdict's budget."""
    pid = recorded.get("pid")
    got = _read(pid if recorded.get("cmdline") else None, sample_ms and max(50, sample_ms))
    out = {"alive": _judge(got, recorded, pid)}
    if out["alive"] and sample_ms:
        out["cpu_cores"] = _cores(got, pid)
        out["descendants"] = len(descendants(pid, got["samples"][-1]))
    return out


def _judge(got, recorded, pid):
    global why
    if got is None or pid is None:
        return None
    row = got["samples"][0].get(pid)
    if row is None:
        return False
    if recorded.get("starttime") is not None and row["starttime"] != recorded["starttime"]:
        return False
    if recorded.get("cmdline"):
        if got["cmdline"] is None:
            why = "pid %d is there with the recorded start time but its command line could not be read" % pid
            return None
        if got["cmdline"] != recorded["cmdline"]:
            return False
    return True


def _cores(got, root):
    if got is None or not got["elapsed"] or got["elapsed"] <= 0:
        return None
    first, second = got["samples"]
    if root not in first:
        return None
    tree = [root] + descendants(root, first)
    before = sum(first[p]["ticks"] for p in tree if p in first)
    after = sum(second[p]["ticks"] for p in tree if p in second)
    return max(0.0, (after - before) / float(got["hz"]) / got["elapsed"])
