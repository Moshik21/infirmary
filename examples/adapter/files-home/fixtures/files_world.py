"""A synthetic files-home: the folder the example adapter reads, a synthetic /proc beside it, and the owner's tidy script as a shim.

Shared by `advice_fixture.py` and `repair_fixture.py` (which `scripts/test` runs: only files named `*_fixture.py` are builders).
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
sys.path.insert(0, os.path.join(ROOT, "examples", "doctor"))
from fixlib import FakeProc, write  # noqa: E402

ADAPTER_PATHS = os.path.join(ROOT, "examples", "adapter")       # what a stranger passes as adapter_paths
NOW = int(time.time())

# the owner's script, as a shim: its documented lines, driven by a small json file; the apply removes the markers it lists
TIDY = r'''#!/usr/bin/env python3
"""Fixture shim of the home's own bin/jobs-tidy: `--dry-run` lists what it would remove, no argument removes it."""
import json, os, sys
cfg = json.load(open(%(cfg)r))
os.makedirs(cfg["state"], exist_ok=True)
if sys.argv[1:] not in ([], ["--dry-run"]):
    print("jobs-tidy: unexpected argument: %%s" %% sys.argv[1], file=sys.stderr)
    sys.exit(2)
dry = sys.argv[1:] == ["--dry-run"]
count = os.path.join(cfg["state"], "dry-runs" if dry else "applies")
n = len(open(count).read().split()) + 1 if os.path.exists(count) else 1
open(count, "a").write("x\n")
listed = cfg["variants"][os.environ.get("TIDY_VARIANT", "default")]
if dry:
    for mid, lane in listed:
        print("would remove job marker %%s (lane %%s has no record)" %% (mid, lane))
    sys.stdout.flush()
    if cfg.get("throw_switch") and n == 1:
        open(os.path.join(os.environ["INFIRMARY_STATE"], "off"), "w").write("off\n")
    if cfg.get("throw_live_off") and n == 1:      # the operator runs `infirmary live off` while the Infirmary is between its readings
        open(os.path.join(os.environ["INFIRMARY_STATE"], "live-repair.json"), "w").write(json.dumps({"schema": "infirmary.live-repair", "enabled": False, "by": "operator"}))
    sys.exit(0)
for mid, lane in listed:
    if cfg.get("clears", True):
        try:
            os.unlink(os.path.join(cfg["home"], "jobs", mid + ".job"))
        except OSError:
            pass
    print("removed job marker %%s" %% mid)
'''


class Home(object):
    """`<root>/home` is the files-home; `<root>/proc` is the synthetic /proc; `<root>/shim-state` is where the owner shim leaves its marks."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.home = os.path.join(self.root, "home")
        self.proc = FakeProc(os.path.join(self.root, "proc"))
        self.proc.add(1, 0, ["/sbin/init"], 900000)
        self.shim_state = os.path.join(self.root, "shim-state")
        self.bindir = os.path.join(self.root, "shims")
        self.lanes_doc = {}
        self.alarms = []
        os.makedirs(self.home, exist_ok=True)
        self.save()

    def lane(self, lane, status=()):
        """A lane in lanes.json with a declaration log: `status` is [(state, seconds ago, text)]."""
        self.lanes_doc[lane] = {"label": lane, "declarations": lane + ".log"}
        write(os.path.join(self.home, lane + ".log"), "".join("%s [at=%d]: %s\n" % (st, NOW - ago, text) for st, ago, text in status))

    def alarm(self, lane, ago, text=""):
        """One recorded alarm, `ago` seconds before now, for a lane (which lanes.json may no longer list)."""
        self.alarms.append({"at": NOW - ago, "lane": lane, "text": text})

    def marker(self, mid, lane):
        write(os.path.join(self.home, "jobs", mid + ".job"), "lane=%s\nstarted=%d\n" % (lane, NOW - 86400))

    def save(self):
        write(os.path.join(self.home, "lanes.json"), json.dumps({"lanes": self.lanes_doc}, indent=1) + "\n")
        write(os.path.join(self.home, "alarms.jsonl"), "".join(json.dumps(a) + "\n" for a in self.alarms))

    def owner(self, listed, variants=None, **cfg):
        """Install the owner's shim at <home>/bin/jobs-tidy: it lists `listed` [(marker id, lane)] (and the `variants` an environment variable picks)."""
        path = os.path.join(self.root, "tidy.json")
        write(path, json.dumps(dict(cfg, state=self.shim_state, home=self.home, variants=dict({"default": listed}, **(variants or {})))))
        write(os.path.join(self.home, "bin", "jobs-tidy"), TIDY % {"cfg": path}, 0o755)

    def mark(self, name):
        return os.path.join(self.shim_state, name)

    def sets(self, extra=()):
        """The settings every run of a scenario takes: this adapter, found through `adapter_paths`, over this home and this synthetic machine."""
        return ["adapter=files-home", "adapter_paths=" + json.dumps([ADAPTER_PATHS]), "adapter_config.home=" + self.home, "host.id=fixture-host",
                "doctor.platform=linux", "doctor.hysteresis=false", "doctor.proc_root=" + self.proc.root, "beat.path=" + os.path.join(self.root, "no-beat.json"),
                "doctor.windows.platform.local=off",
                "doctor.disk.paths=" + json.dumps([{"path": self.root, "label": "fixture", "warn_gb": 0, "crit_gb": 0, "warn_pct": 0, "crit_pct": 0}])] + list(extra)

    def args(self, verb, *extra, sets=True):
        out = [verb] + list(extra)
        for s in (self.sets() if sets else []):
            out += ["--set", s]
        return out

    def env(self, **kw):
        out = {"PATH": os.pathsep.join([self.bindir, os.environ.get("PATH", "")])}
        out.update(kw)
        return out
