"""The unknowns of a check: what could not be decided, with the exact reason, as a short list every surface can show.

A check that cannot decide answers `unknown`, breaks (`error`) or overruns its budget. The Infirmary never turns that into a green tile, a
blank card or a missing row: each distinct unknown is ONE entry here, and the status card, the Lookout feed, `advice.txt` and
`check` all say so from this list. Nothing here reads anything: it folds a `doctor.json` document that is already in hand, so it costs
nothing and acts on nothing.

An entry is `{"id", "check", "title", "class", "reason", "next", "host"}`:

  could-not-read  the check answered `unknown`: an input could not be read, or the evidence did not decide (the check says which)
  check-broke     the check raised an error (its type and message are the reason)
  overran         a probe of another host did not finish within `doctor.remote_budget_ms`
  rule-unknown    a finding whose own status is UNKNOWN (a host whose probe failed, a machine whose feed is last-known): the finding says why

The id is stable (`unknown:<check>:<class>`, or the finding's id for a rule-unknown), so one unknown is one card however often the Infirmary
runs, and two checks with the same words are two cards. A check that is `skipped` (nothing applies here, or it is switched off) is not
an unknown: it says why in its own row and is never counted here.
"""
COULD_NOT_READ, CHECK_BROKE, OVERRAN, RULE_UNKNOWN = "could-not-read", "check-broke", "overran", "rule-unknown"
CLASSES = {
    COULD_NOT_READ: "the check could not read an input, or what it read did not decide",
    CHECK_BROKE: "the check raised an error",
    OVERRAN: "a probe of another host did not finish within the remote budget",
    RULE_UNKNOWN: "a finding the Infirmary could not establish",
}
NEXT = {
    COULD_NOT_READ: "make the source the reason names readable, or turn the check off with doctor.disabled where it does not apply to this home; until then it says nothing either way",
    CHECK_BROKE: "read the error in the reason; the check says nothing either way until it is fixed or turned off with doctor.disabled",
    OVERRAN: "look at the host the check probes (it answers slowly or not at all), or raise doctor.remote_budget_ms; until then the check says nothing either way",
    RULE_UNKNOWN: "read the finding's reason and follow its next step",
}
NO_REASON = "the check gave no reason"
LIMIT = 300


def _clip(text, n=LIMIT):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def class_of(check):
    """The class of a check row (doctor.json `checks[]`) whose state is `unknown` or `error`."""
    if check["state"] == "unknown":
        return COULD_NOT_READ
    return check.get("reason_class") or CHECK_BROKE


def of(doc):
    """The unknowns of a doctor.json document, the checks' rows first, then the findings: one entry per distinct unknown, never a blank reason."""
    host = doc["host"]["id"]
    by_check = {}
    for f in doc["findings"]:
        by_check.setdefault(f["check"], []).append(f)
    out, seen = [], set()
    for c in doc["checks"]:
        if c["state"] not in ("unknown", "error"):
            continue
        cls = class_of(c)
        uid = "unknown:%s:%s" % (c["check"], cls)
        if uid in seen:
            continue
        seen.add(uid)
        # an incident rule's own unknown row (incident-unreadable) knows the next step best; it stands behind this entry, not beside it
        cover = [f for f in by_check.get(c["check"], []) if f["kind"] == "incident-unreadable"]
        out.append({"id": uid, "check": c["check"], "title": c["title"], "class": cls, "reason": _clip(c["detail"]) or NO_REASON,
                    "next": _clip(cover[0]["recommendation"] if cover and cover[0]["recommendation"] else NEXT[cls]), "host": host})
    for f in doc["findings"]:
        if f["status"] != "UNKNOWN" or f["pending"] or f["kind"] == "incident-unreadable" or f["id"] in seen:
            continue
        seen.add(f["id"])
        card = f.get("card") or {}
        out.append({"id": f["id"][:200], "check": f["check"], "title": f["title"], "class": RULE_UNKNOWN,
                    "reason": _clip(card.get("unknown") or f["why"]) or NO_REASON, "next": _clip(card.get("next_step") or f["recommendation"] or NEXT[RULE_UNKNOWN]),
                    "host": f["host"]})
    return out


def line(u, width=160):
    """One plain line for a text surface: `check · class · reason`."""
    return "%s · %s · %s" % (u["check"], u["class"], _clip(u["reason"], width))

