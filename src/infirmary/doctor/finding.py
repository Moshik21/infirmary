"""One finding per snag, in the shape every check, card and `doctor.json` row shares.

Shaped on the verdict contract of `infirmary.verdict` (name, status, subject, why, evidence,
recommendation) so a supervising watcher can read it the same way. `recommendation` is text for a
person, never a command; `how` carries the owning script's own dry run, as an argv array, that is only
ever shown unless `doctor.dry_runs` is on. A finding is advice. It never names an action the Infirmary takes.
"""
import hashlib
import shlex
import time

from infirmary import VERSION

FINDING_SCHEMA = "infirmary.finding"
FINDING_SCHEMA_VERSION = "1.2"
DOCTOR_SCHEMA = "infirmary.doctor"
DOCTOR_SCHEMA_VERSION = "1.7"

# status rank decides the exit code (worst wins); unknown outranks warning, never critical
RANK = {"OK": 0, "WARNING": 1, "UNKNOWN": 2, "CRITICAL": 3}
EXIT = {"OK": 0, "WARNING": 10, "UNKNOWN": 50, "CRITICAL": 30}
KINDS = ("machine", "service", "disk", "capacity", "info", "decision")


def make(check, catalogue, kind, key, status, host, subject, title, why, evidence=None, how=None, recommendation="", lk_kind="service",
         retires_with=None, qualifiers=None, hows=None, target=None, card=None):
    """`kind` names the snag (also the hysteresis key); `key` tells two findings of one kind apart. `target` is what a repair
    would act on, as the check saw it (a process identity, a path), so the repair can prove it is still the same thing.
    `card` (1.2) is the incident card of a failure that has actually happened (`card()` below, docs/incidents.md): a confirmed
    finding that carries one becomes one append-only incident row."""
    return {
        "schema": FINDING_SCHEMA, "schema_version": FINDING_SCHEMA_VERSION, "name": "infirmary", "tool_version": VERSION,
        "id": "%s:%s" % (kind, key), "check": check, "catalogue": catalogue, "kind": kind, "lookout_kind": lk_kind,
        "status": status, "host": host, "subject": subject, "title": title, "why": why,
        "evidence": list(evidence or []), "how": how, "hows": list(hows or []), "recommendation": recommendation,
        "retires_with": list(retires_with or []), "qualifiers": list(qualifiers or []),
        "tier": "T0", "pending": False, "first_seen": None, "age_s": 0, "target": target, "card": card,
    }


def fact(source, text, ref=None):
    return {"source": source, "fact": text, "ref": ref}


def rec(record, fact_text, at=None):
    """One record an incident card stands on: what was read, what it said, and when that record was written (epoch seconds, or None when
    the record cannot be dated: the row then says the age is unknown instead of guessing one)."""
    return {"record": record, "fact": fact_text, "at": int(at) if at is not None else None}


RISK = ("yes", "no", "unknown")


def card(what, next_step, since=None, since_basis=None, blocking=None, risk="unknown", risk_why=None, records=None, unknown=None):
    """The plain-words incident card (docs/incidents.md): `what` happened, `since` when (epoch, with the record it comes from in
    `since_basis`), what it is `blocking`, whether work is at risk (`yes`, `no` or `unknown`, and why), the `records` it rests on
    (`rec()`), and the ONE safe `next_step`, as text and never run. `unknown` is the reason when the input could not be read: the card
    is then an unknown card, and every field that depends on the missing input says so rather than guess."""
    if risk not in RISK:
        raise ValueError("risk is yes, no or unknown")
    return {"what": what, "next_step": next_step, "since": int(since) if since is not None else None, "since_basis": since_basis,
            "blocking": blocking, "work_at_risk": {"state": risk, "why": risk_why}, "records": list(records or []), "unknown": unknown}


def how(owner, note, dry_run=None, cwd=None):
    """The owning script and its dry run. `dry_run` is an argv list or None; `cwd` is where it is run."""
    return {"owner": owner, "note": note, "dry_run": list(dry_run) if dry_run else None, "cwd": cwd, "dry_run_output": None}


def all_hows(f):
    return [h for h in [f.get("how")] + list(f.get("hows") or []) if h]


def how_text(f):
    """The "how" line of a finding: each owner's dry run (shown, not run unless opted in) and what to do next."""
    bits = []
    for h in all_hows(f):
        if h.get("dry_run"):
            cmd = " ".join(shlex.quote(a) for a in h["dry_run"])
            bits.append("dry run%s: %s" % (" (in %s)" % h["cwd"] if h.get("cwd") else "", cmd))
        if h.get("note"):
            bits.append(h["note"])
    return "; ".join(bits)


def key_of(*parts):
    return hashlib.sha1("\0".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def line(f):
    pend = " (watching, not yet confirmed)" if f.get("pending") else ""
    return "%s · %s · %s%s" % (f["status"].lower(), f["subject"], f["title"], pend)


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))
