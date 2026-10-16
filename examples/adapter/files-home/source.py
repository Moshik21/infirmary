"""The files-home example adapter's source: a home that is a folder of plain files.

    <home>/lanes.json      the lanes, in the shape of the built-in `files` source (docs/sources.md)
    <home>/<lane>.log      each lane's declaration log, named by its lanes.json entry
    <home>/alarms.jsonl    the alarms a watcher recorded, one JSON object per line: {"at": EPOCH, "lane": "lane-a", "text": "idle 3600s, possible wedge"}

It adds to the built-in source only what `infirmary advise` and `infirmary install` need: `recorded_alarms` (what to explain), `outcome`
(what each lane did next, for the judged ledger) and `ADVICE_DIR` (where an install keeps its files). Read-only: nothing here writes
under the home. Setting: `adapter_config.home`, the folder above.
"""
import json
import os
import re
import time

from infirmary.core import declarations
from infirmary.explain import evidence
from infirmary.explain.sources import FilesSource, SourceError

IDLE = re.compile(r"idle (\d+)s")


def home_of(cfg):
    home = (cfg.get("adapter_config") or {}).get("home")
    if not home:
        raise SourceError("the files-home adapter needs adapter_config.home (the folder that holds lanes.json)")
    return os.path.abspath(os.path.expanduser(home))


class Source(FilesSource):
    ADVICE_DIR = "infirmary"       # `infirmary install --home HOME` keeps its files in <home>/infirmary

    def __init__(self, cfg):
        home = home_of(cfg)
        if not os.path.isfile(os.path.join(home, "lanes.json")):
            raise SourceError("no lanes.json in %s (set adapter_config.home)" % home)
        FilesSource.__init__(self, dict(cfg, files={"lanes": os.path.join(home, "lanes.json")}))
        self.home = home

    @staticmethod
    def parse_alarm(text):
        """An alarm's text -> what the engine reads as its trigger: how long the lane had been idle, when the text says so."""
        trig = {"kind": "silence", "text": (text or "")[:300]}
        m = IDLE.search(text or "")
        if m:
            trig.update(kind="wedge-timer", idle_s=int(m.group(1)))
        return trig

    def collect(self, subject, clock, trigger=None, as_of=None):
        if subject["id"] in (self.doc.get("lanes") or {}):
            return FilesSource.collect(self, subject, clock, trigger=trigger, as_of=as_of)
        ev = evidence.empty(subject, int(as_of or time.time()))     # an alarm for a lane that has no record in lanes.json: it was taken down
        ev["trigger"] = trigger or {}
        ev["lane"] = {"known": True, "closed": True, "note": "lanes.json has no entry for it"}
        return ev

    def recorded_alarms(self, since=None, until=None):
        """The alarms in alarms.jsonl whose moment falls in [since, until]: [{n, at, window, text, subject, trigger}], `n` counting the file's alarms."""
        out, n = [], 0
        try:
            with open(os.path.join(self.home, "alarms.jsonl"), encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return out
        for raw in lines:
            try:
                row = json.loads(raw)
                at, lane, text = int(row["at"]), str(row["lane"]), str(row.get("text") or "")
            except (ValueError, KeyError, TypeError):
                continue
            n += 1
            if (since is not None and at < since) or (until is not None and at > until):
                continue
            out.append({"n": n, "at": at, "window": None, "text": text, "subject": {"id": lane, "label": lane}, "trigger": self.parse_alarm(text)})
        return out

    def outcome(self, lane, at, until):
        """What the lane's own declaration log shows inside (at, until]: -> {"events": [{kind, at, record}], "instructed": False, "unreadable": [why]}."""
        spec = (self.doc.get("lanes") or {}).get(lane)
        if spec is None:
            return {"events": [], "instructed": False, "unreadable": ["lanes.json has no entry for the lane, so there is no log to read"]}
        rows = declarations.read(self._p(spec["declarations"])) if spec.get("declarations") else None
        if rows is None:
            return {"events": [], "instructed": False, "unreadable": ["the lane's declaration log cannot be read"]}
        kinds = {"done": "finished", "failed": "finished", "resolved": "answered"}
        return {"events": [{"kind": kinds.get(r["state"], "moved"), "at": r["at"], "record": "declaration log: %s" % r["state"]}
                           for r in rows if r["at"] is not None and at < r["at"] <= until], "instructed": False, "unreadable": []}
