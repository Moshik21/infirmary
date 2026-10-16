"""Source loading and the built-in generic `files` source.

A source turns "a lane" into an evidence dict. The contract (docs/sources.md):

    class Source:
        def __init__(self, cfg): ...
        def lanes(self): -> [{"id", "label"}]
        def resolve(self, lane=None, window=None): -> subject dict or None
        def collect(self, subject, clock, trigger=None, as_of=None): -> evidence dict

Adapters for a specific tool live outside the core, in <adapter_paths entry>/<name>/source.py, and are the only place that tool's
names may appear. The `files` source reads a hand-written lanes.json and needs no tool at all.
"""
import glob
import importlib.util
import json
import os
import re
import sys
import time

from infirmary.core import beat, declarations, proc
from infirmary.explain import evidence

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class SourceError(Exception):
    pass


def load(cfg):
    name = cfg.get("adapter") or "files"
    if name == "files":
        return FilesSource(cfg)
    dirs = [os.path.expanduser(p) for p in cfg.get("adapter_paths", [])] + [os.path.join(REPO, "adapters")]
    for base in dirs:
        path = os.path.join(base, name, "source.py")
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("infirmary_adapter_" + re.sub(r"\W", "_", name), path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            return mod.Source(cfg)
    raise SourceError("no adapter %r (looked in %s)" % (name, ", ".join(dirs)))


class FilesSource:
    """Lanes described in one JSON file; see examples/demo-fleet/lanes.example.json."""

    def __init__(self, cfg):
        path = (cfg.get("files") or {}).get("lanes")
        if not path:
            raise SourceError("the files source needs files.lanes (path to lanes.json); try --demo first")
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as fh:
                self.doc = json.load(fh)
        except (OSError, ValueError) as exc:
            raise SourceError("cannot read lanes file %s: %s" % (path, exc))
        self.base = os.path.dirname(os.path.abspath(os.path.expanduser(path)))
        self.cfg = cfg

    def _p(self, p):
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.join(self.base, p)

    def lanes(self):
        return [{"id": k, "label": v.get("label") or k} for k, v in sorted((self.doc.get("lanes") or {}).items())]

    def resolve(self, lane=None, window=None):
        lanes = self.doc.get("lanes") or {}
        if lane in lanes:
            return {"id": lane, "label": lanes[lane].get("label") or lane}
        if window:
            for k, v in lanes.items():
                if v.get("window") == window:
                    return {"id": k, "label": v.get("label") or k}
        return None

    def collect(self, subject, clock, trigger=None, as_of=None):
        now = as_of or time.time()
        spec = self.doc["lanes"][subject["id"]]
        th = self.cfg["thresholds"]
        ev = evidence.empty(subject, int(now))
        ev["trigger"] = trigger or {}
        t0 = time.monotonic()
        if spec.get("declarations"):
            rows = declarations.read(self._p(spec["declarations"]), as_of=as_of)
            ev["declarations"] = rows or []
            evidence.mark(ev, "declarations", "ok" if rows is not None else "unavailable", (time.monotonic() - t0) * 1000)
        t0 = time.monotonic()
        ag = spec.get("agent")
        if ag:
            alive = proc.same(ag)
            ev["agent"] = {"alive": alive, "source": "process identity", "state": ag.get("state", "unknown"), "pid": ag.get("pid")}
            if alive and clock.left() > th["cpu_sample_ms"] / 1000.0 + 0.1:
                ev["agent"]["cpu_cores"] = proc.cpu_cores(ag["pid"], th["cpu_sample_ms"])
                ev["agent"]["descendants"] = len(proc.descendants(ag["pid"]))
                if ev["agent"]["cpu_cores"] is not None and ev["agent"]["cpu_cores"] >= th["busy_cpu_cores"]:
                    ev["agent"]["state"] = "busy"
            elif alive:
                evidence.mark(ev, "process sample", "timeout", 0, "no time left for the CPU sample")
            evidence.mark(ev, "process", "ok" if alive is not None else "unavailable", (time.monotonic() - t0) * 1000)
        if spec.get("activity"):
            ev["activity"] = evidence.activity([self._p(p) for p in spec["activity"]], now, th["activity_fresh_s"])
            ev["activity"]["source"] = "activity globs"
        self._queues(ev, spec, subject["id"], now)
        for name, hv in (self.doc.get("hosts") or {}).items():
            b = beat.read(self._p(hv["beat"]), now) if hv.get("beat") else None
            if b:
                ev["hosts"][name] = {"beat_age_s": b["age_s"], "level": b["level"], "canary_ok": hv.get("canary_ok", True)}
        return ev

    def _queues(self, ev, spec, lane_id, now):
        for cls in spec.get("queue_classes") or []:
            q = (self.doc.get("queues") or {}).get(cls)
            if not q:
                continue
            tasks = set(spec.get("tasks") or [lane_id])
            for path in sorted(glob.glob(self._p(q.get("holders", "")))):
                try:
                    kv = evidence.parse_kv(open(path, encoding="utf-8", errors="replace").read().strip())
                except OSError:
                    continue
                if kv.get("task") not in tasks:
                    continue
                pid = int(kv["pid"]) if (kv.get("pid") or "").isdigit() else None
                alive = proc.same({"pid": pid}) if pid else None
                if alive is False:
                    continue
                at = int(kv["at"]) if (kv.get("at") or "").isdigit() else int(os.path.getmtime(path))
                ev["jobs"].append({"kind": "holder", "class": cls, "host": kv.get("host"), "tier": kv.get("tier"), "started": at,
                                   "held_s": max(0, int(now - at)), "cmd": kv.get("cmd", ""), "cmd_key": evidence.cmd_key(kv.get("cmd", "")),
                                   "alive": alive, "source": "slot holder file"})
            ticket_re = re.compile(q.get("ticket_re") or r"^(?P<tier>\d)-(?P<arrival>\d+)-(?P<pid>\d+)$")
            tickets = []
            for path in glob.glob(self._p(q.get("tickets", ""))):
                m = ticket_re.match(os.path.basename(path))
                if m:
                    tickets.append((m.group("tier"), int(m.group("arrival")), int(m.group("pid"))))
            tickets.sort()
            apid = (spec.get("agent") or {}).get("pid")
            owned = set(proc.descendants(apid)) if apid else set()   # a ticket counts only when a descendant of the agent holds it
            for i, (tier, arrival, pid) in enumerate(tickets):
                if pid in owned:
                    ev["jobs"].append({"kind": "ticket", "class": cls, "tier": tier, "ahead": i, "held_s": 0, "alive": proc.same({"pid": pid}),
                                       "source": "slot ticket"})
