"""Helpers the Infirmary's fixture builders share: a synthetic /proc tree and small file writers.

A fixture builder (`fixture.py` here, and one per adapter) writes a directory tree plus `scenarios.json` that
`scripts/test` replays through the public `infirmary` command. Nothing here is imported by the product.
"""
import json
import os
import time

CLK = 100


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    if mode:
        os.chmod(path, mode)
    return path


def touch_age(path, age_s):
    t = time.time() - age_s
    os.utime(path, (t, t))


class FakeProc(object):
    """A synthetic /proc: process directories, `stat` (btime), `meminfo` and `pressure/memory`."""

    def __init__(self, root, uptime_s=1000000):
        self.root, self.now = root, time.time()
        self.btime = int(self.now - uptime_s)
        os.makedirs(root, exist_ok=True)
        write(os.path.join(root, "stat"), "cpu  1 2 3 4\nbtime %d\n" % self.btime)
        self.memory()

    def add(self, pid, ppid, argv, age_s, cwd=None, env=None, rss_mb=50, comm=None, fds=None, ticks=0):
        base = os.path.join(self.root, str(pid))
        start_ticks = int((self.now - age_s - self.btime) * CLK)
        fields = ["S", str(ppid), str(ppid), "0", "0", "0", "0", "0", "0", "0", "0", str(ticks), "0", "0", "0", "20", "0", "1", "0", str(start_ticks), "0", "0"]
        write(os.path.join(base, "stat"), "%d (%s) %s\n" % (pid, comm or os.path.basename(argv[0]), " ".join(fields)))
        write(os.path.join(base, "cmdline"), "\0".join(argv) + "\0")
        write(os.path.join(base, "statm"), "0 %d 0 0 0 0 0\n" % (rss_mb * 256))
        write(os.path.join(base, "environ"), "".join("%s=%s\0" % kv for kv in sorted((env or {}).items())))
        if cwd:
            os.symlink(cwd, os.path.join(base, "cwd"))
        for n, target in enumerate(fds or [], 3):       # open files, as the links under /proc/<pid>/fd
            os.makedirs(os.path.join(base, "fd"), exist_ok=True)
            os.symlink(target, os.path.join(base, "fd", str(n)))
        return start_ticks

    def memory(self, total_gb=16.0, avail_pct=50.0, swap_gb=8.0, swap_used_pct=5.0, psi_full=0.1):
        kb = int(total_gb * 1048576)
        write(os.path.join(self.root, "meminfo"), "MemTotal: %d kB\nMemFree: %d kB\nMemAvailable: %d kB\nSwapTotal: %d kB\nSwapFree: %d kB\n" % (
            kb, int(kb * avail_pct / 100.0 / 2), int(kb * avail_pct / 100.0), int(swap_gb * 1048576), int(swap_gb * 1048576 * (1 - swap_used_pct / 100.0))))
        write(os.path.join(self.root, "pressure", "memory"),
              "some avg10=%.2f avg60=%.2f avg300=%.2f total=1\nfull avg10=%.2f avg60=%.2f avg300=%.2f total=1\n" % ((psi_full,) * 3 + (psi_full,) * 3))


def scenario(name, runs, servers=None):
    return {"name": name, "runs": runs, "servers": servers or []}


def run(args, expect, env=None, text=False, seen=None, setup=None):
    return {"args": args, "expect": expect, "env": env or {}, "text": text, "seen": seen or {}, "setup": setup or []}


def save(outdir, scenarios):
    with open(os.path.join(outdir, "scenarios.json"), "w") as fh:
        json.dump({"scenarios": scenarios}, fh, indent=1)
