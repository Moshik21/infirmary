"""The files-home example adapter's catalogue: one check, one incident rule and one repair action, all over plain files.

The core loads `hooks(cfg)` from here (docs/sources.md) and adds what it returns to its own checks:

    job-markers    (90)  a check: a file under <home>/jobs/ names a lane that lanes.json does not have: a leftover of a lane taken down
    lane-failed    (91)  an incident rule: a lane's own declaration log says `failed` (one card, one row in incidents.jsonl, per lane)
    tidy-job-markers     an action: the home's own `bin/jobs-tidy` removes the leftover markers, after its dry run lists exactly the ones found

The action never touches a marker itself: it runs the owner's script (`<home>/bin/jobs-tidy --dry-run`, then `<home>/bin/jobs-tidy`)
and verifies by reading the markers again. Write your own adapter by replacing these three with checks and owners of your tool.
"""
import glob
import hashlib
import json
import os
import re

from infirmary.core import declarations
from infirmary.doctor import finding, registry, repair

LISTED = re.compile(r"^would remove job marker (\S+) \(lane (\S+) has no record\)$")


def _home(cfg):
    home = (cfg.get("adapter_config") or {}).get("home")
    return os.path.abspath(os.path.expanduser(home)) if home else None


def _lanes(home):
    """{lane: its lanes.json entry}, or None when lanes.json cannot be read (a check then says unknown, never fine)."""
    try:
        with open(os.path.join(home, "lanes.json"), encoding="utf-8") as fh:
            lanes = json.load(fh)["lanes"]
        return lanes if isinstance(lanes, dict) and all(isinstance(v, dict) for v in lanes.values()) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _markers(home):
    """[(id, path, lane, sha)] for each <home>/jobs/*.job: a `lane=NAME` line, and the digest of the file as read."""
    out = []
    for path in sorted(glob.glob(os.path.join(home, "jobs", "*.job"))):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        m = re.search(r"^lane=(\S+)", raw.decode("utf-8", "replace"), re.M)
        out.append((os.path.basename(path)[:-len(".job")], path, m.group(1) if m else None, hashlib.sha1(raw).hexdigest()))
    return out


def make_check_markers(home):
    owner = os.path.join(home, "bin", "jobs-tidy")

    def check_markers(ctx):
        lanes = _lanes(home)
        if lanes is None:
            return registry.unknown("cannot read %s, so no marker can be matched to a lane" % os.path.join(home, "lanes.json"))
        markers = _markers(home)
        if not markers:
            return registry.ok("no job markers under %s (nothing to tidy)" % os.path.join(home, "jobs"))
        how = finding.how("jobs-tidy", "Its dry run lists the markers it would remove and removes nothing. With live repair on the Infirmary runs it once the dry run "
                          "lists exactly the markers found here.", [owner, "--dry-run"])
        out = []
        for mid, path, lane, sha in markers:
            if lane in lanes:
                continue
            out.append(finding.make(
                "job-markers", 90, "leftover-job-marker", mid, "WARNING", ctx.host_id, "job marker %s" % mid,
                "a job marker names a lane that has no record", "%s names lane %s, which lanes.json does not list; nothing will ever clear it" % (path, lane or "(none)"),
                evidence=[finding.fact("jobs directory", "%s holds lane=%s" % (path, lane or "(none)"), path), finding.fact("lanes.json", "no lane %s among %d" % (lane, len(lanes)))],
                how=how, recommendation="Let the owner's tidy script remove it after reading its dry-run list.", lk_kind="service",
                target={"id": mid, "path": path, "lane": lane, "sha": sha}))
        return registry.found(out, "%d job marker(s), %d name a lane with no record" % (len(markers), len(out)))
    return check_markers


def make_check_failed_lane(home):
    def check_failed_lane(ctx):
        lanes = _lanes(home)
        if lanes is None:
            return registry.unknown("cannot read %s, so no lane's declaration log can be found" % os.path.join(home, "lanes.json"),
                                    next_step="make lanes.json readable (adapter_config.home is where it is looked for), or turn this rule off with doctor.disabled")
        out, read = [], 0
        for lane, spec in sorted(lanes.items()):
            if not spec.get("declarations"):
                continue
            path = os.path.join(home, os.path.expanduser(spec["declarations"]))
            rows = declarations.read(path)
            if rows is None:
                continue
            read += 1
            kind, row = declarations.effective(rows)
            if kind != "failed":
                continue
            what = "lane %s declared it failed%s" % (lane, ": " + row["text"] if row["text"] else "")
            card = finding.card(what, "read the lane's log, then relaunch it or close it with a `resolved` line", since=row["at"], since_basis="the lane's own `failed` line",
                                blocking="whatever waits on lane %s" % lane, risk="unknown", risk_why="this rule reads only the declaration log, not the lane's files",
                                records=[finding.rec("declaration log %s" % path, "newest line: failed", row["at"])])
            out.append(finding.make("lane-failed", 91, "lane-failed", lane, "WARNING", ctx.host_id, "lane %s" % lane, "a lane declared it failed", what,
                                    evidence=[finding.fact("declaration log", "%s: failed" % path, path)], recommendation=card["next_step"], card=card))
        return registry.found(out, "%d declaration log(s) read, %d lane(s) failed" % (read, len(out)))
    return check_failed_lane


class TidyJobMarkers(repair.Action):
    name = "tidy-job-markers"
    catalogue = 90
    kinds = ("leftover-job-marker",)
    tier = "T1"
    owner = "jobs-tidy"
    title = "remove job markers whose lane has no record"
    max_targets = 16

    def __init__(self, home):
        self.home = home
        self.script = os.path.join(home, "bin", "jobs-tidy")

    def propose(self, ctx, findings):
        return [repair.proposal(self, findings, [self.script], dry_run=[self.script, "--dry-run"],
                                note="the owner removes each listed marker itself; the Infirmary removes nothing")]

    def assess(self, ctx, prop, dry, now):
        listed, odd = [], []
        for line in dry.out.splitlines():
            line = line.strip()
            m = LISTED.match(line)
            if m:
                listed.append((m.group(1), m.group(2)))
            elif line:
                odd.append(line)
        seen = dict((t["id"], t) for t in prop["seen"].values() if t)
        cur = dict((f["target"]["id"], f["target"]) for f in now.values() if f["kind"] in self.kinds and f.get("target"))
        lanes = _lanes(self.home) or {}
        extra = [i for i, _ in listed if i not in seen]
        moved = [i for i, _ in listed if i in seen and (cur.get(i) or {}).get("sha") != seen[i]["sha"]]
        wrong = [i for i, lane in listed if i in seen and lane != seen[i]["lane"]]
        alive = [i for i, lane in listed if lane in lanes]
        checks = [
            repair.check("the owner's dry run prints only lines it documents", not odd, odd[0][:120] if odd else None),
            repair.check("the dry run lists at least one marker (an empty list never widens scope)", listed, len(listed)),
            repair.check("the dry run lists at most %d markers" % self.max_targets, len(listed) <= self.max_targets, len(listed)),
            repair.check("every marker it lists is one the check found", not extra, ", ".join(extra) or None),
            repair.check("each listed marker is the file the check read (same content)", not moved, "%s changed" % ", ".join(moved) if moved else None),
            repair.check("each listed marker names the lane the check saw", not wrong, ", ".join(wrong) or None),
            repair.check("each listed lane still has no record", not alive, "%s has a record again" % ", ".join(alive) if alive else None),
        ]
        return repair.assessment(["marker %s" % i for i, _ in listed], checks)


def make_check_unset(what):
    def check_unset(ctx):
        return registry.unknown("adapter_config.home is not set, so no %s can be read" % what,
                                next_step="set adapter_config.home to the folder that holds lanes.json")
    return check_unset


def hooks(cfg):
    home = _home(cfg)
    if home is None:
        return {"checks": [registry.Spec("job-markers", 90, "Job markers", make_check_unset("job marker")),
                           registry.Spec("lane-failed", 91, "Failed lanes", make_check_unset("declaration log"), family="incident")],
                "actions": []}
    return {"checks": [registry.Spec("job-markers", 90, "Job markers", make_check_markers(home)),
                       registry.Spec("lane-failed", 91, "Failed lanes", make_check_failed_lane(home), family="incident")],
            "actions": [TidyJobMarkers(home)]}
