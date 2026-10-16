"""What a check is given: configuration, the clock, the host, and cached readers. Checks do not reach for globals."""
import glob
import os
import threading
import time

from infirmary.core import execute, hostid, sysinfo, beat as beatmod


class Clock(object):
    def __init__(self, budget_ms):
        self.t0 = time.time()
        self.budget = budget_ms / 1000.0

    def left(self):
        return self.budget - (time.time() - self.t0)

    def elapsed_ms(self):
        return int((time.time() - self.t0) * 1000)


class Ctx(object):
    def __init__(self, cfg, now=None):
        self.cfg = cfg
        self.d = cfg["doctor"]
        self.now = time.time() if now is None else now
        self.host = hostid.identity(cfg)
        self.osk = self.d.get("platform") or self.host["os"]
        self.proc_root = self.d.get("proc_root") or "/proc"
        self.clock = Clock(self.d["tick_budget_ms"])
        self._procs = None
        self._beat = False
        self._once_lock = threading.Lock()
        self._once = {}
        self.adapter = {}        # filled by the runner: hooks an adapter offers other checks (inventory paths, dry runs)

    @property
    def host_id(self):
        return self.host["id"]

    def once(self, key, fn):
        """Run `fn` once per tick for `key` and give every caller the same answer: the checks of one host share a single ssh probe
        (checks of other hosts run in threads, so a second caller waits for the first instead of asking the host again)."""
        with self._once_lock:
            ent = self._once.get(key)
            if ent is None:
                ent = self._once[key] = [threading.Lock(), None, False]
        with ent[0]:
            if not ent[2]:
                ent[1], ent[2] = fn(), True
            return ent[1]

    def run(self, argv, timeout=5.0, env=None, cwd=None, input_text=None):
        return execute.run(argv, timeout=timeout, env=env, cwd=cwd, input_text=input_text)

    def processes(self):
        """One process listing shared by every check of this tick."""
        if self._procs is None:
            self._procs = sysinfo.processes(self.proc_root, self.osk, ps_run=self.run)
        return self._procs

    def have_processes(self):
        """False where this OS gives no process table (no /proc, no ps): checks on processes then say unknown, never fine."""
        return bool(self.processes())

    def my_processes(self):
        uid = os.getuid() if hasattr(os, "getuid") else None
        return [p for p in self.processes() if uid is None or p["uid"] in (None, uid)]

    def beat(self):
        """The host beat, when lookout-beat is running here. None is 'no beat', never 'all fine'."""
        if self._beat is False:
            self._beat = beatmod.read(self.cfg["beat"]["path"] or beatmod.default_path(), now=self.now)
        return self._beat

    def memory(self):
        return sysinfo.memory(self.proc_root, self.osk, run=self.run)

    def expand(self, path):
        return os.path.expanduser(path)

    def globs(self, patterns):
        out = []
        for pat in patterns or []:
            out += sorted(glob.glob(os.path.expanduser(pat)))
        return out
