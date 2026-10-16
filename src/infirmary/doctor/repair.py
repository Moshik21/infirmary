"""Repairs: tier-1 actions the Infirmary runs by calling an owner's script, tier-2 actions written as inert plans a person approves.

The action set is closed and lives in code (an adapter's `actions` hook, one `Action` per owner command). Configuration can
only hold an action back (`doctor.repair.actions.<name>`: `plan` = never run it unattended, `off` = never propose it); it
cannot add an action, change an argv or widen a target. Nothing here starts a program except through `core.execute` (argv
arrays, no shell, a timeout), and the only programs started are an action's own dry run and its own repair command.

A tier-1 run, in `repair` mode only (which exists only while the operator's live-repair toggle is on for the home), goes:

    gates        kill switch, the live-repair toggle, the host's circuit breaker (many open findings, or a critical host), the action's breaker
                 (failed or unverified runs in a row), repair flapping, the rate limits, the Infirmary-wide lock
    reading 1    the owner's dry run, and the action's own preconditions over what it listed (empty or oversized target
                 sets are refused: an empty set never widens scope)
    reading 2    immediately before acting: the kill switch and the toggle again, the finding detected again from scratch, the dry run
                 again; the targets must be exactly the targets of reading 1
    act          the owner's repair command, once
    verify       the finding detected again: the repair is done only when the check no longer finds it

Every run, refusal, plan and approval is one line in `<state>/actions.jsonl` with its reason and both readings, and the
outcomes of the last tick are Lookout cards. A tier-2 action (or a tier-1 action whose own preconditions call for a person)
is written to `<state>/plans/<id>.json`; `infirmary approve <id>` re-verifies everything and runs exactly that argv once,
or, for an action its owner carries out, records the approval and prints the handoff.
"""
import hashlib
import json
import os
import time

from infirmary.core import actions as actlog, execute, live as livemod, state
from infirmary.doctor import finding

PLAN_SCHEMA = "infirmary.plan"
PLAN_SCHEMA_VERSION = "1.0"
RAN = ("verified", "not-cleared", "failed")       # outcomes of an owner command that actually ran
FAILED = ("not-cleared", "failed")


class Action(object):
    """One owner command the Infirmary may run (tier 1) or plan (tier 2). Subclasses fill the class attributes and `propose`."""
    name = ""            # stable id, also the config key and the plan prefix
    catalogue = 0
    kinds = ()           # the finding kinds it answers
    tier = "T1"          # T1: run unattended in repair mode; T2: only ever a plan
    runs = "doctor"      # doctor: `approve` runs it; owner: the owner carries the approved plan out (the Infirmary runs nothing)
    owner = ""           # who owns the command, for people
    title = ""
    max_targets = 1      # more targets than this in the dry run is refused
    dry_rc = (0,)        # exit codes of the dry run that mean "it answered" (a check mode may exit 1 while it reports gaps)

    def propose(self, ctx, findings):
        """[proposal(...)] for these confirmed findings of this host. Builds argv only from code and from what the checks saw."""
        return []

    def assess(self, ctx, prop, dry, now):
        """Read the dry run (`dry` is an execute.Result, or None for an action without one) against the findings as they are
        `now` ({id: finding}). Return assessment(targets, checks, escalate=...)."""
        return assessment([], [])

    def verify(self, ctx, prop, result, after, targets):
        """After the command ran: (cleared, detail). Default: none of the proposal's findings is detected any more."""
        left = [fid for fid in prop["findings"] if fid in after]
        return (not left, "the check no longer finds it" if not left else "still found: %s" % ", ".join(left))


def proposal(action, findings, apply, dry_run=None, cwd=None, note="", handoff=""):
    """What an action would do for these findings. `seen` keeps each finding's target as the check saw it."""
    return {"action": action.name, "findings": sorted(f["id"] for f in findings), "checks": sorted(set(f["check"] for f in findings)),
            "subject": ", ".join(sorted(set(f["subject"] for f in findings)))[:200], "titles": sorted(set(f["title"] for f in findings)),
            "dry_run": list(dry_run) if dry_run else None, "apply": list(apply), "cwd": cwd, "note": note, "handoff": handoff,
            "seen": dict((f["id"], f.get("target")) for f in findings)}


def assessment(targets, checks, escalate=None, note=""):
    """`targets` are what the command would touch (sorted, comparable between readings); `checks` are [check(...)];
    `escalate` names why a person must decide even though the action is tier 1 (the run becomes a plan)."""
    return {"targets": sorted(targets), "checks": list(checks), "escalate": escalate, "note": note}


def check(name, ok, value=None):
    return {"name": name, "ok": bool(ok), "value": value}


# ---- the Infirmary's own repair memory: <state>/repairs.json --------------------------------------------------------------
def _hist_path(sd):
    return os.path.join(sd, "repairs.json")


def load_history(sd):
    try:
        with open(_hist_path(sd), encoding="utf-8") as fh:
            h = json.load(fh)
        if isinstance(h, dict):
            h.setdefault("runs", [])
            h.setdefault("breakers", {})
            return h
    except (OSError, ValueError):
        pass
    return {"runs": [], "breakers": {}}


def save_history(sd, h, now):
    h["runs"] = [r for r in h["runs"] if now - r.get("ts", 0) <= 7 * 86400][-500:]
    try:
        os.makedirs(sd, exist_ok=True)
        tmp = _hist_path(sd) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(h, fh)
        os.replace(tmp, _hist_path(sd))
    except OSError:
        pass


def breaker_state(h, name, rc, now):
    b = h["breakers"].get(name) or {}
    failures = b.get("failures", 0)
    tripped = failures >= rc["breaker_failures"] and now - b.get("last_failure", 0) < rc["breaker_cooldown_s"]
    return {"failures": failures, "tripped": tripped, "until": int(b.get("last_failure", 0) + rc["breaker_cooldown_s"]) if tripped else None}


def _note_run(h, name, prop, outcome, now):
    h["runs"].append({"ts": int(now), "action": name, "findings": prop["findings"], "outcome": outcome})
    b = h["breakers"].setdefault(name, {"failures": 0})
    if outcome == "verified":
        b["failures"] = 0
    elif outcome in FAILED:
        b["failures"] = b.get("failures", 0) + 1
        b["last_failure"] = int(now)


# ---- the Infirmary-wide lock: one risky action at a time ------------------------------------------------------------------
class _Lock(object):
    def __init__(self, sd):
        self.fh = None
        try:
            os.makedirs(sd, exist_ok=True)
            self.fh = open(os.path.join(sd, "repair.lock"), "a+")
        except OSError:
            return
        try:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:
            try:
                import msvcrt
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.close()
        except OSError:
            self.close()

    def close(self):
        if self.fh:
            self.fh.close()
        self.fh = None


# ---- one tick of repair mode ---------------------------------------------------------------------------------------------
class Engine(object):
    def __init__(self, cfg, loaded, sd, ctx, redetect, now=None):
        self.cfg, self.loaded, self.sd, self.ctx, self._redetect = cfg, loaded, sd, ctx, redetect
        self.rc = cfg["doctor"]["repair"]
        self.now = time.time() if now is None else now
        self.actions = dict((a.name, a) for a in (loaded[0].get("actions") or []))
        self.hist = load_history(sd)

    def live_on(self):
        """The operator's live-repair toggle, read from the home's state each time: turning it off stops a repair in flight, as the
        kill switch does. Without it a tier-1 action is never run (`approve` is a person's act and does not need it)."""
        return livemod.read(self.cfg)["on"]

    def held(self, name):
        return (self.rc.get("actions") or {}).get(name)

    def tier_of(self, act):
        return "T2" if act.tier == "T2" or self.held(act.name) == "plan" else "T1"

    def redetect(self, prop):
        return self._redetect(prop["checks"])

    def log(self, kind, act, prop, tier, **fields):
        actlog.record(self.sd, "infirmary", kind, self.cfg["mode"], tier=tier, action=act.name, findings=prop["findings"],
                      argv=prop["apply"], dry_run=prop["dry_run"], cwd=prop["cwd"], **fields)

    def _tail(self, res):
        n = self.rc["output_lines"]
        lines = ((res.out or "") + ("\n" + res.err if res.err else "")).strip().splitlines()
        return [l[:300] for l in lines[-n:]]

    def reading(self, act, prop, now_findings, ctx=None):
        """One reading: the owner's dry run, then the action's preconditions over what it listed and over the findings as they
        are now. `now_findings` may be a function returning (findings, ctx): it is then called after the dry run, so the
        identity re-check is the last thing read before the act. Returns a dict with `refusal` set when it says no."""
        out = {"at": round(time.time(), 3), "dry_run": None, "targets": [], "checks": [], "escalate": None, "refusal": None, "note": ""}
        dry = None
        if prop["dry_run"]:
            dry = execute.run(prop["dry_run"], timeout=self.cfg["doctor"]["dry_run_timeout_s"], cwd=prop["cwd"])
            out["dry_run"] = {"rc": dry.rc, "timed_out": dry.timed_out, "missing": dry.missing, "took_ms": dry.took_ms, "tail": self._tail(dry)}
            if dry.missing:
                out["refusal"] = "the owner's dry run cannot be started (%s is not installed or not executable)" % prop["dry_run"][0]
                return out
            if dry.timed_out or dry.rc not in act.dry_rc:
                out["refusal"] = "the owner's dry run %s" % ("timed out" if dry.timed_out else "exited %s" % dry.rc)
                return out
        if callable(now_findings):
            now_findings, ctx = now_findings()
            out["found"] = sorted(fid for fid in prop["findings"] if fid in now_findings)
            gone = [fid for fid in prop["findings"] if fid not in now_findings]
            if gone:
                out["refusal"] = "%s no longer found when re-checked" % ", ".join(gone)
                out["gone"] = gone
                return out
        a = act.assess(ctx or self.ctx, prop, dry, now_findings)
        out.update(targets=a["targets"], checks=a["checks"], escalate=a["escalate"], note=a["note"])
        bad = [c for c in a["checks"] if not c["ok"]]
        if bad:
            out["refusal"] = "; ".join("%s%s" % (c["name"], " (%s)" % c["value"] if c["value"] not in (None, "") else "") for c in bad)
        return out

    def gates(self, act, prop, open_n, host_critical):
        """Why a tier-1 run may not start now, or None. Every reason names the number behind it."""
        rc, now = self.rc, self.now
        if state.is_off(self.cfg):
            return "the kill switch is on"
        if not self.live_on():
            return "live repair is off for this home (`infirmary live on` is the operator's switch)"
        if host_critical:
            return "circuit breaker: the host is critical (%s); no unattended repair on a struggling host" % host_critical
        if open_n >= rc["breaker_open_findings"]:
            return "circuit breaker: %d findings are open on this host (limit %d); many snags at once mean something bigger, so a person looks first" % (
                open_n, rc["breaker_open_findings"])
        b = breaker_state(self.hist, act.name, rc, now)
        if b["tripped"]:
            return "circuit breaker: %s failed or did not clear its finding %d time(s) in a row; held until %s or `infirmary reset %s`" % (
                act.name, b["failures"], finding.now_iso(b["until"]), act.name)
        hour = [r for r in self.hist["runs"] if now - r["ts"] < 3600 and r["outcome"] in RAN]
        for fid in prop["findings"]:
            fixed = [r for r in self.hist["runs"] if now - r["ts"] < rc["flap_window_s"] and r["outcome"] == "verified" and fid in r["findings"]]
            if len(fixed) >= rc["flap_repairs"]:
                return "repair keeps recurring: %s was repaired %d times within %s; this is a bug to fix, not a snag to repair again" % (
                    fid, len(fixed), _span(rc["flap_window_s"]))
        if len(hour) >= rc["host_per_hour"]:
            return "rate limit: %d repair(s) on this host in the last hour (limit %d)" % (len(hour), rc["host_per_hour"])
        mine = [r for r in hour if r["action"] == act.name]
        if len(mine) >= rc["action_per_hour"]:
            return "rate limit: %s ran %d time(s) in the last hour (limit %d)" % (act.name, len(mine), rc["action_per_hour"])
        for fid in prop["findings"]:
            n = len([r for r in hour if fid in r["findings"]])
            if n >= rc["finding_per_hour"]:
                return "rate limit: %s was acted on %d time(s) in the last hour (limit %d per finding)" % (fid, n, rc["finding_per_hour"])
        return None

    # ---- outcomes ----------------------------------------------------------------------------------------------------
    def _outcome(self, act, prop, tier, outcome, reason, **extra):
        row = {"action": act.name, "title": act.title, "owner": act.owner, "tier": tier, "outcome": outcome, "reason": reason,
               "findings": prop["findings"], "subject": prop["subject"], "argv": prop["apply"], "dry_run": prop["dry_run"], "cwd": prop["cwd"]}
        row.update(extra)
        return row

    def refuse(self, act, prop, tier, reason, **readings):
        self.log("refusal", act, prop, tier, outcome="refused", reason=reason, **readings)
        return self._outcome(act, prop, tier, "refused", reason)

    def run_t1(self, act, prop, open_n, host_critical):
        why = self.gates(act, prop, open_n, host_critical)
        if why:
            return self.refuse(act, prop, "T1", why)
        lock = _Lock(self.sd)
        if lock.fh is None:
            return self.refuse(act, prop, "T1", "another repair holds the Infirmary's lock (one risky action at a time)")
        try:
            r1 = self.reading(act, prop, self.findings)
            if r1["refusal"]:
                return self.refuse(act, prop, "T1", "precondition: " + r1["refusal"], reading_1=r1)
            if r1["escalate"]:
                plan, fresh = self.write_plan(act, prop, r1, escalated=r1["escalate"])
                return self._outcome(act, prop, "T2", "planned", "a person decides: %s" % r1["escalate"], plan=plan["id"], new=fresh)
            # reading 2, immediately before acting
            if state.is_off(self.cfg):
                return self.refuse(act, prop, "T1", "the kill switch was thrown after the first reading", reading_1=r1)
            if not self.live_on():
                return self.refuse(act, prop, "T1", "live repair was turned off after the first reading", reading_1=r1)
            r2 = self.reading(act, prop, lambda: self.redetect(prop))
            if r2.get("gone") and len(r2["gone"]) == len(prop["findings"]):
                self.log("refusal", act, prop, "T1", outcome="cleared", reason="the finding cleared on its own before the act", reading_1=r1, reading_2=r2)
                return self._outcome(act, prop, "T1", "cleared", "the finding cleared on its own before the act; nothing ran")
            if r2["refusal"]:
                return self.refuse(act, prop, "T1", "precondition (re-check): " + r2["refusal"], reading_1=r1, reading_2=r2)
            if r2["targets"] != r1["targets"]:
                return self.refuse(act, prop, "T1", "the targets changed between the two readings (%s, then %s)" % (_list(r1["targets"]), _list(r2["targets"])),
                                   reading_1=r1, reading_2=r2)
            if r2["escalate"]:
                return self.refuse(act, prop, "T1", "the re-check now needs a person: %s" % r2["escalate"], reading_1=r1, reading_2=r2)
            if state.is_off(self.cfg):
                return self.refuse(act, prop, "T1", "the kill switch was thrown before the act", reading_1=r1, reading_2=r2)
            if not self.live_on():
                return self.refuse(act, prop, "T1", "live repair was turned off before the act", reading_1=r1, reading_2=r2)
            return self.execute(act, prop, "T1", r1, r2)
        finally:
            lock.close()

    def execute(self, act, prop, tier, r1, r2, approved_by=None, plan_id=None):
        acted_at = round(time.time(), 3)
        res = execute.run(prop["apply"], timeout=self.rc["timeout_s"], cwd=prop["cwd"])
        after, ctx3 = self.redetect(prop)
        cleared, detail = act.verify(ctx3, prop, res, after, r2["targets"])
        if res.missing or res.timed_out or res.rc != 0:
            outcome = "failed"
            reason = "the owner's command %s" % ("could not be started" if res.missing else "timed out" if res.timed_out else "exited %s" % res.rc)
        else:
            outcome = "verified" if cleared else "not-cleared"
            reason = detail if cleared else "the command exited 0 but %s" % detail
        _note_run(self.hist, act.name, prop, outcome, time.time())
        save_history(self.sd, self.hist, time.time())
        b = breaker_state(self.hist, act.name, self.rc, time.time())
        tail = self._tail(res)
        self.log("repair", act, prop, tier, outcome=outcome, reason=reason, acted_at=acted_at, rc=res.rc, timed_out=res.timed_out, took_ms=res.took_ms, output=tail,
                 reading_1=r1, reading_2=r2, verified=outcome == "verified", approved_by=approved_by, plan=plan_id, breaker_tripped=b["tripped"])
        return self._outcome(act, prop, tier, outcome, reason, rc=res.rc, output=tail[-6:], breaker_tripped=b["tripped"])

    # ---- plans ---------------------------------------------------------------------------------------------------------
    def write_plan(self, act, prop, r1, escalated=None):
        key = json.dumps([act.name, prop["findings"], prop["apply"], prop["cwd"], r1["targets"]], sort_keys=True)
        pid = "%s-%s" % (act.name, hashlib.sha1(key.encode("utf-8")).hexdigest()[:8])
        old = read_plan(self.sd, pid)
        if old and old.get("state") == "proposed" and not _expired(old, time.time()):
            return old, False
        now = time.time()
        plan = {"schema": PLAN_SCHEMA, "schema_version": PLAN_SCHEMA_VERSION, "id": pid, "created_at": finding.now_iso(now), "created_epoch": int(now),
                "expires_epoch": int(now + self.rc["plan_ttl_s"]), "host": self.ctx.host_id, "action": act.name, "catalogue": act.catalogue,
                "title": act.title, "tier": "T2", "runs": act.runs, "owner": act.owner, "findings": prop["findings"], "checks": prop["checks"],
                "subject": prop["subject"], "titles": prop["titles"], "dry_run": prop["dry_run"], "apply": prop["apply"], "cwd": prop["cwd"],
                "targets": r1["targets"], "reading": r1, "escalated": escalated, "note": prop["note"], "handoff": prop["handoff"],
                "state": "proposed", "approve": "infirmary approve %s" % pid}
        _save_plan(self.sd, plan)
        self.log("plan", act, prop, "T2", outcome="planned", reason=escalated or "tier 2: a person approves", plan=pid, targets=r1["targets"], reading_1=r1)
        return plan, True

    def plan_t2(self, act, prop):
        if state.is_off(self.cfg):
            return self.refuse(act, prop, "T2", "the kill switch is on")
        r1 = self.reading(act, prop, self.findings)
        if r1["refusal"]:
            return self.refuse(act, prop, "T2", "no plan: " + r1["refusal"], reading_1=r1)
        plan, fresh = self.write_plan(act, prop, r1, escalated=r1["escalate"] if act.tier == "T1" else None)
        return self._outcome(act, prop, "T2", "planned", "plan %s waits for a person" % plan["id"], plan=plan["id"], new=fresh)

    def proposals(self, findings):
        """(action, proposal) for every confirmed finding of this host an action answers."""
        out = []
        for act in self.actions.values():
            if self.held(act.name) == "off":
                continue
            fs = [f for f in findings if f["kind"] in act.kinds and f["host"] == self.ctx.host_id and not f["pending"]]
            if fs:
                for p in act.propose(self.ctx, fs) or []:
                    out.append((act, p))
        return out

    def tick(self, doc, write_plans_only=False, select=None):
        """Run repair mode over one check's findings. Returns the `repairs` block of doctor.json."""
        self.findings = dict((f["id"], f) for f in doc["findings"])
        live = [f for f in doc["findings"] if not f["pending"]]
        mode = self.cfg["mode"]
        host = self.ctx.host_id
        open_n = len([f for f in live if f["host"] == host])
        b = doc.get("beat") or {}
        crit = []
        if b.get("level") == "critical":
            crit.append("host beat level critical")
        crit += ["%s" % f["title"] for f in live if f["host"] == host and f["kind"] == "memory-pressure" and f["status"] == "CRITICAL"]
        outcomes = []
        for act, prop in self.proposals(live):
            if select and not (set(select) & set(prop["findings"])):
                continue
            tier = self.tier_of(act)
            if write_plans_only:
                outcomes.append(self.plan_t2(act, prop))
            elif mode != "repair":
                what = ("would run %s after its dry run" % _argv(prop["apply"])) if tier == "T1" else "would write a plan for a person to approve"
                outcomes.append(self._outcome(act, prop, tier, "advised", "advise mode: %s" % what))
            elif tier == "T1":
                outcomes.append(self.run_t1(act, prop, open_n, "; ".join(crit)))
            else:
                outcomes.append(self.plan_t2(act, prop))
        return {"mode": mode, "repair_mode": livemod.label(self.cfg), "outcomes": outcomes, "plans": open_plans(self.sd), "breakers": self.breakers()}

    def breakers(self):
        out = {}
        for name in sorted(set(list(self.actions) + list(self.hist["breakers"]))):
            out[name] = breaker_state(self.hist, name, self.rc, time.time())
        return out


# ---- plan files ------------------------------------------------------------------------------------------------------------
def plans_dir(sd):
    return os.path.join(sd, "plans")


def _safe_id(pid):
    return bool(pid) and all(c.isalnum() or c in "-_." for c in pid) and not pid.startswith(".")


def read_plan(sd, pid):
    if not _safe_id(pid):
        return None
    try:
        with open(os.path.join(plans_dir(sd), pid + ".json"), encoding="utf-8") as fh:
            p = json.load(fh)
        return p if isinstance(p, dict) else None
    except (OSError, ValueError):
        return None


def _save_plan(sd, plan):
    os.makedirs(plans_dir(sd), exist_ok=True)
    path = os.path.join(plans_dir(sd), plan["id"] + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _expired(plan, now):
    return now > plan.get("expires_epoch", 0)


def all_plans(sd):
    out = []
    try:
        names = sorted(os.listdir(plans_dir(sd)))
    except OSError:
        return out
    now = time.time()
    for n in names:
        if n.endswith(".json"):
            p = read_plan(sd, n[:-5])
            if p:
                if p.get("state") == "proposed" and _expired(p, now):
                    p["state"] = "expired"
                out.append(p)
    return out


def open_plans(sd):
    return [{"id": p["id"], "action": p["action"], "runs": p["runs"], "owner": p["owner"], "subject": p["subject"], "titles": p.get("titles", []),
             "targets": p["targets"], "apply": p["apply"], "cwd": p["cwd"], "created_at": p["created_at"], "escalated": p.get("escalated"),
             "approve": p["approve"]} for p in all_plans(sd) if p.get("state") == "proposed"]


def approve(cfg, loaded, sd, ctx, redetect, pid, by):
    """Re-verify a plan and run exactly its argv once (or record the handoff for an owner-run plan). Returns an outcome dict."""
    eng = Engine(cfg, loaded, sd, ctx, redetect)
    plan = read_plan(sd, pid)
    if plan is None:
        return {"outcome": "refused", "reason": "no plan %r in %s" % (pid, plans_dir(sd)), "plan": pid}
    fake = {"findings": plan.get("findings", []), "apply": plan.get("apply"), "dry_run": plan.get("dry_run"), "cwd": plan.get("cwd"),
            "subject": plan.get("subject", ""), "checks": plan.get("checks", [])}

    class _Named(object):
        name, title, owner = plan.get("action", "?"), plan.get("title", ""), plan.get("owner", "")

    def no(reason, stale=False):
        if stale:
            plan["state"] = "stale"
            plan["stale_reason"] = reason
            _save_plan(sd, plan)
        actlog.record(sd, "infirmary", "refusal", cfg["mode"], tier="T2", action=plan.get("action"), findings=fake["findings"], argv=fake["apply"],
                      plan=pid, outcome="refused", reason=reason, approved_by=by)
        return {"outcome": "refused", "reason": reason, "plan": pid, "action": plan.get("action")}

    if state.is_off(cfg):
        return no("the kill switch is on")
    if plan.get("state") != "proposed":
        return no("this plan is %s, not waiting for approval (a plan runs at most once)" % plan.get("state"))
    if _expired(plan, time.time()):
        return no("this plan expired at %s; the next check writes a fresh one if the finding is still there" % finding.now_iso(plan["expires_epoch"]), stale=True)
    act = eng.actions.get(plan.get("action"))
    if act is None:
        return no("%r is not an action this Infirmary knows (the action set is closed, in code)" % plan.get("action"))
    if eng.held(act.name) == "off":
        return no("%s is switched off in the configuration" % act.name)
    now_f, ctx2 = redetect(plan.get("checks") or [])
    gone = [fid for fid in plan["findings"] if fid not in now_f]
    if gone:
        return no("%s no longer found: nothing to repair" % ", ".join(gone), stale=True)
    eng.findings = now_f
    props = act.propose(ctx2, [now_f[fid] for fid in plan["findings"]]) or []
    match = [p for p in props if p["apply"] == plan["apply"] and p["dry_run"] == plan["dry_run"] and p["cwd"] == plan["cwd"] and p["findings"] == sorted(plan["findings"])]
    if not match:
        return no("the plan's command is not the one the Infirmary would build now (%s); it is never run as written" % _argv(plan["apply"]), stale=True)
    prop = match[0]
    lock = _Lock(sd)
    if lock.fh is None:
        return no("another repair holds the Infirmary's lock (one risky action at a time)")
    try:
        r = eng.reading(act, prop, lambda: redetect(plan.get("checks") or []))
        if r["refusal"]:
            return no("precondition: " + r["refusal"], stale=True)
        if r["targets"] != plan["targets"]:
            return no("the dry run now lists %s, the plan listed %s" % (_list(r["targets"]), _list(plan["targets"])), stale=True)
        if state.is_off(cfg):
            return no("the kill switch was thrown before the act")
        if act.runs == "owner":
            plan.update(state="approved", approved_by=by, approved_at=finding.now_iso())
            _save_plan(sd, plan)
            eng.log("approve", act, prop, "T2", outcome="approved", reason="approved for %s to carry out" % act.owner, plan=pid, approved_by=by, reading=r)
            return {"outcome": "approved", "plan": pid, "action": act.name, "reason": "approved; %s carries it out: %s%s" % (
                act.owner, _argv(prop["apply"]), " (in %s)" % prop["cwd"] if prop["cwd"] else ""), "handoff": prop["handoff"]}
        out = eng.execute(act, prop, "T2", plan.get("reading"), r, approved_by=by, plan_id=pid)
        plan.update(state="done" if out["outcome"] == "verified" else "failed", approved_by=by, approved_at=finding.now_iso(), result=out["outcome"])
        _save_plan(sd, plan)
        out["plan"] = pid
        return out
    finally:
        lock.close()


def reset(cfg, sd, name, by):
    h = load_history(sd)
    was = breaker_state(h, name, cfg["doctor"]["repair"], time.time())
    h["breakers"][name] = {"failures": 0}
    save_history(sd, h, time.time())
    actlog.record(sd, "infirmary", "reset", cfg["mode"], action=name, was_tripped=was["tripped"], failures=was["failures"], by=by)
    return was


def _argv(argv):
    import shlex
    return " ".join(shlex.quote(a) for a in argv or [])


def _list(xs):
    xs = [str(x) for x in xs]
    return "[%s]" % ", ".join(xs[:6] + (["... %d more" % (len(xs) - 6)] if len(xs) > 6 else [])) if xs else "nothing"


def _span(secs):
    return "%dh" % (secs // 3600) if secs >= 3600 else "%dm" % (secs // 60)
