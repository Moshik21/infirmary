"""The Infirmary's findings as a Lookout feed (`lookout.feed 1.0`), a source of its own.

Every confirmed finding is an `attention` card (kind machine, service, disk or capacity; `how` says where to act, as the
board is read-only). Each check is a `collectors[]` entry, so a check that could not run shows as unavailable on the
board instead of silently empty. A host the Infirmary flags is reported as `draining` or `lost` through `machines[].health`.
A finding still inside its `for` duration (pending) is not sent. A check that could not decide (unknown, broke or overran its
budget) is one `info` card of its own with the exact reason (`unknowns.py`), never only a collector row: the board says unknown, not nothing.
"""
import time

from infirmary import VERSION
from infirmary.doctor import finding, unknowns

DRAIN_KINDS = ("disk-low", "memory-pressure", "tmpfs-full", "host-memory-short", "wsl-vm-near-cap", "wsl-vm-spinning", "remote-canary-failing", "remote-draining")
SEVERITY = {"CRITICAL": "critical", "WARNING": "action", "UNKNOWN": "info"}
LIMIT = 30
COLLECTORS = 20


def _t(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def build(doc, cfg, seq, boot_id):
    host = doc["host"]["id"]
    sid = cfg["doctor"]["lookout_source_id"]
    live = [f for f in doc["findings"] if not f["pending"]]
    unk = doc["unknowns"] if "unknowns" in doc else unknowns.of(doc)
    live = [f for f in live if f["kind"] != "incident-unreadable"]      # a rule that could not read its input is the unknown card below, not a second one
    cards = []
    for f in live[:LIMIT]:
        title = f["title"] if f["subject"] in f["title"] else "%s: %s" % (f["subject"], f["title"])
        c = f.get("card")
        detail, since, how = f["why"], f["first_seen"] or doc["at_epoch"], finding.how_text(f) or f["recommendation"]
        if c:      # an incident card: the plain words, the onset the records give, and the one safe next step
            risk = c["work_at_risk"]
            detail = "%s Blocking: %s. Work at risk: %s%s." % (c["what"], c["blocking"] or "unknown (%s)" % (c.get("unknown") or "not established"), risk["state"],
                                                             " (%s)" % risk["why"] if risk.get("why") else "")
            since, how = c["since"] if c["since"] is not None else since, "Next: %s" % c["next_step"]
        cards.append({"id": f["id"][:200], "kind": f["lookout_kind"], "severity": SEVERITY[f["status"]], "title": title[:300],
                      "detail": detail[:1900], "since": _t(since), "machine": f["host"], "how": how[:1900] or None})
    cards += unknown_cards([u for u in unk if u["class"] != unknowns.RULE_UNKNOWN], host, doc)      # a rule-unknown is its finding's card above
    cards += repair_cards(doc.get("repairs"), host, doc)
    machines = []
    for hid, fl in sorted(doc["flags"].items()):
        row = {"id": hid, "label": hid, "as_of": doc["checked_at"]}
        if fl.get("hung"):
            row["health"] = {"state": "lost", "since": doc["checked_at"], "reason": "the Infirmary found this host hung (no ssh reply while it accepts connections)"}
        elif fl.get("drain"):
            why = sorted(set(f["title"] for f in live if f["host"] == hid and f["kind"] in DRAIN_KINDS))[:2]
            row["health"] = {"state": "draining", "since": doc["checked_at"], "reason": ("the Infirmary flags this host to drain: " + "; ".join(why))[:300] if why else "the Infirmary flags this host to drain"}
        else:
            continue
        machines.append(row)
    cols = []
    for c in doc["checks"]:
        st = {"ok": "ok", "finding": "ok", "skipped": "disabled"}.get(c["state"], "error")
        col = {"id": c["check"], "as_of": doc["checked_at"], "status": st}
        if st == "error":
            col["error"] = ("%s: %s" % (unknowns.class_of(c), c["detail"] or unknowns.NO_REASON))[:500]
        elif st == "disabled":
            col["warnings"] = [(c["detail"] or "skipped; the check gave no reason")[:300]]      # why nothing was checked, so a quiet row is not mistaken for a clean one
        cols.append(col)
    if len(cols) > COLLECTORS:
        # the board takes 20 collectors and rejects more: the last ones share one, so none is dropped silently
        keep, rest = cols[:COLLECTORS - 1], cols[COLLECTORS - 1:]
        bad = [c for c in rest if c["status"] == "error"]
        agg = {"id": "more-checks", "as_of": doc["checked_at"], "status": "error" if bad else "ok" if any(c["status"] == "ok" for c in rest) else "disabled"}
        if bad:
            agg["error"] = ("; ".join("%s: %s" % (c["id"], c.get("error", "error")) for c in bad))[:500]
        agg["warnings"] = ["covers %d checks: %s" % (len(rest), ", ".join(c["id"] for c in rest))[:300]]
        cols = keep + [agg]
    feed = {"schema": "lookout.feed", "schema_version": "1.0",
            "source": {"id": sid, "label": "Infirmary · %s" % (doc.get("repair_mode") or doc["mode"]), "machine": host, "kind": "standalone"},
            "producer": {"name": "infirmary", "version": VERSION, "boot_id": boot_id, "seq": seq, "mode": "live"},
            "generated_at": doc["checked_at"], "interval_s": 60, "collect_ms": doc["cost"]["took_ms"],
            "collectors": cols, "machines": machines[:12], "items": [], "attention": cards}
    if len(cards) > LIMIT:
        feed["attention"] = cards[:LIMIT]
        feed["omitted"] = [{"surface": "attention", "shown": LIMIT, "total": len(cards), "reason": "cap"}]
    return feed


def unknown_cards(unk, host, doc):
    """One `info` card per distinct unknown of a check that has no finding of its own: the exact reason, what it means, and the next step."""
    return [{"id": u["id"][:200], "kind": "service", "severity": "info", "title": ("%s: unknown (%s)" % (u["check"], u["class"]))[:300],
             "detail": ("%s. This is not a pass: the Infirmary could not decide, and says nothing either way." % u["reason"].rstrip("."))[:1900],
             "since": doc["checked_at"], "machine": u["host"] or host, "how": ("Next: %s" % u["next"])[:1900]} for u in unk]


REPAIR_SEVERITY = {"refused": "action", "failed": "action", "not-cleared": "action", "verified": "info", "cleared": "info", "approved": "info"}


def repair_cards(rep, host, doc):
    """Every repair run and refusal of this tick is a card, and every plan waiting for a person is a `decision` card."""
    if not rep:
        return []
    out = []
    for o in rep.get("outcomes") or []:
        sev = REPAIR_SEVERITY.get(o["outcome"])
        if not sev:
            continue
        word = {"verified": "repaired", "refused": "repair refused", "failed": "repair failed", "not-cleared": "repair did not clear",
                "cleared": "cleared before repair", "approved": "repair approved"}[o["outcome"]]
        key = "%s|%s" % (o["action"], ",".join(o["findings"]))
        out.append({"id": ("repair:%s:%s" % (o["action"], finding.key_of(key)))[:200], "kind": "service", "severity": sev,
                    "title": ("%s: %s (%s)" % (word, o["subject"], o["action"]))[:300], "detail": o["reason"][:1900],
                    "since": doc["checked_at"], "machine": host,
                    "how": ("infirmary status; the actions log has both readings" if o["outcome"] != "verified" else "nothing to do; logged in actions.jsonl")})
    for p in rep.get("plans") or []:
        out.append({"id": ("plan:%s" % p["id"])[:200], "kind": "decision", "severity": "action",
                    "title": ("approve or leave: %s (%s)" % (p["subject"], p["action"]))[:300],
                    "detail": ("%s%s" % ("runs " + " ".join(p["apply"]) if p["runs"] == "doctor" else "carried out by %s: %s" % (p["owner"], " ".join(p["apply"])),
                                        "; " + p["escalated"] if p.get("escalated") else ""))[:1900],
                    "since": p["created_at"], "machine": host, "how": p["approve"]})
    return out
