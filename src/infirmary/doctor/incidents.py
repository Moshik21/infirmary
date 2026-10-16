"""Incident cards: one append-only row for each failure that has actually happened (docs/incidents.md).

A check of the incident family (`Spec.family == "incident"`) raises ordinary findings, and a finding that carries a card
(`finding.card`) and has been confirmed (its `for` duration is over) is an incident. This module turns it into one row:

    what happened, since when, what it is blocking, whether work is at risk, the records it rests on with their ages,
    and the ONE safe next step, as text. Nothing here acts on a row, and nothing acts on the next step.

Rows go to `<state>/incidents.jsonl`, in the family of the value ledger: append-only, one line per record, never rotated and never
edited, an unknown shown as unknown with its reason. A row is appended once, when its incident is first confirmed; its id is
`<kind>:<key>@<first seen>`, so every later tick that still sees the incident finds the id in the file and writes nothing.
`<state>/incidents.json` is the snapshot of what is open now (rewritten by each run that read every incident rule) and is what a board or a
supervisor reads.

A family check that could not read its input, or that broke, never goes quiet: `cover_unreadable` gives it an unknown finding and
therefore an unknown row, which says what could not be read and what to do about it.

It reads only what the checks already read: the cost is one small read of the ledger's tail and one short append per new incident.
"""
import json
import os
import tempfile
import time

from infirmary import VERSION
from infirmary.doctor import finding

SCHEMA = "infirmary.incident"
SCHEMA_VERSION = "1.0"
SNAPSHOT_SCHEMA = "infirmary.incidents"
LEDGER = "incidents.jsonl"
SNAPSHOT = "incidents.json"
FAMILY = "incident"
UNREADABLE = "incident-unreadable"
NEXT_UNREADABLE = ("make the source readable (the detail says which one), or turn the rule off with doctor.disabled where it does not apply to "
                   "this home; until then this rule says nothing either way")


def paths(sd):
    return {"ledger": os.path.join(sd, LEDGER), "snapshot": os.path.join(sd, SNAPSHOT)}


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _ago(secs):
    secs = int(max(0, secs))
    if secs >= 86400:
        return "%dd%02dh" % (secs // 86400, secs % 86400 // 3600)
    if secs >= 3600:
        return "%dh%02dm" % (secs // 3600, secs % 3600 // 60)
    if secs >= 90:
        return "%dm" % (secs // 60)
    return "%ds" % secs


def _hm(epoch):
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(epoch))


def _clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---- the unknown card: a rule that could not read its input -------------------------------------------------------------------------
def cover_unreadable(results, host):
    """A family check that errored, overran, or answered `unknown` without a finding gets one unknown finding, so the failure to
    read is a row and not a silence. A skipped check (nothing applies here) stays skipped. Run before the for/clear durations."""
    for r in results:
        if r.get("family") != FAMILY or r["state"] not in ("error", "unknown") or r["findings"]:
            continue
        extra = r.get("extra") or {}
        why = r["detail"] or ("the check broke" if r["state"] == "error" else "the check could not decide")
        what = "the rule %s could not read its input, so it can say nothing about the failure it watches for: %s" % (r["check"], _clip(why, 200))
        card = finding.card(what, extra.get("next_step") or NEXT_UNREADABLE, since_basis=extra.get("since_basis"), blocking=None, risk="unknown",
                            risk_why="not read: " + _clip(why, 120), records=extra.get("records") or [], unknown=_clip(why, 300))
        f = finding.make(r["check"], r["catalogue"], UNREADABLE, r["check"], "UNKNOWN", host, r["check"],
                         "%s could not be read" % r["title"], what, evidence=[finding.fact("check %s" % r["check"], "%s: %s" % (r["state"], why))],
                         recommendation=card["next_step"], card=card)
        r["findings"] = [f]


# ---- the row -------------------------------------------------------------------------------------------------------------------------
def row_of(f, now, host=None):
    """The incident row for a confirmed finding that carries a card."""
    c = f["card"]
    first = f.get("first_seen") or int(now)
    since, basis = c.get("since"), c.get("since_basis")
    if since is None:
        since, basis = first, basis or "first seen by this check; the onset is not recorded"
    unknown = c.get("unknown") or ("the check could not decide" if f["status"] == "UNKNOWN" else None)
    blocking = c.get("blocking")
    risk = dict(c.get("work_at_risk") or {"state": "unknown", "why": None})
    if risk["state"] == "unknown" and not risk.get("why"):
        risk["why"] = "the card names no record that shows it"
    return {
        "v": 1, "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "id": "%s@%d" % (f["id"], first), "tool": "infirmary",
        "kind": f["kind"], "check": f["check"], "catalogue": f["catalogue"], "status": "unknown" if unknown else "open",
        "severity": f["status"].lower(), "host": host or f["host"], "subject": f["subject"],
        "what": c["what"], "since": int(since), "since_basis": basis, "first_seen": int(first),
        "blocking": {"text": blocking, "unknown": None if blocking else (unknown or "the card names nothing it blocks")},
        "work_at_risk": risk,
        "evidence": [{"record": e["record"], "age_s": int(max(0, now - e["at"])) if e.get("at") is not None else None,
                      "age_unknown": None if e.get("at") is not None else "the record carries no time", "fact": e["fact"]} for e in c.get("records") or []],
        "next_step": c["next_step"], "unknown": unknown,
        "read_at": int(now), "reader": "infirmary/%s" % VERSION, "supersedes": None,
    }


# ---- the ledger ----------------------------------------------------------------------------------------------------------------------
def _snapshot_ids(sd):
    """The ids the previous snapshot records as already in the ledger: an incident that stays open keeps its id however far its row has
    scrolled out of the ledger's tail."""
    try:
        with open(paths(sd)["snapshot"], encoding="utf-8") as fh:
            ids = json.load(fh).get("in_ledger")
    except (OSError, ValueError, AttributeError):
        return set()
    return {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()


def _known_ids(path, tail_bytes, sd=None):
    """The ids already in the ledger: those in the tail of the file, and those the previous snapshot records as in it. None when the
    file exists and cannot be read."""
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            fh.seek(max(0, size - tail_bytes))
            data = fh.read()
    except FileNotFoundError:
        return set()
    except OSError:
        return None
    ids = _snapshot_ids(sd) if sd else set()
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            ids.add(json.loads(line)["id"])
        except (ValueError, KeyError, TypeError):
            continue
    return ids


def append(sd, rows, tail_bytes, max_bytes):
    """Append the rows whose id is not in the ledger yet: ([ids appended], [ids already there], [problems]). Append-only: the file is
    opened for append, never truncated, rotated or rewritten, and a full ledger refuses (visibly) instead of dropping old rows."""
    p = paths(sd)["ledger"]
    new, old, problems = [], [], []
    if not rows:
        return new, old, problems         # the ledger is created by its first row, not by a run that found nothing
    try:
        os.makedirs(sd, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    except OSError as exc:
        return [], [], ["%s: %s" % (p, exc)]
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)     # two runs at once (check and advise) must not both append one id
        except (ImportError, OSError):
            pass
        known = _known_ids(p, tail_bytes, sd)
        if known is None:
            return [], [], ["%s: the ledger cannot be read to tell which incidents it already holds, so nothing was appended" % p]
        for r in rows:
            if r["id"] in known:
                old.append(r["id"])
                continue
            if os.fstat(fd).st_size >= max_bytes:
                problems.append("%s: the ledger is at its size limit (%d KiB); %s was not appended" % (p, max_bytes // 1024, r["id"]))
                continue
            line = (json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            if os.write(fd, line) != len(line):
                problems.append("%s: a short write for %s" % (p, r["id"]))
                continue
            new.append(r["id"])
            known.add(r["id"])
    except OSError as exc:
        problems.append("%s: %s" % (p, exc))
    finally:
        os.close(fd)
    return new, old, problems


def _write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".incidents.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---- the fold: findings -> rows -> ledger and snapshot --------------------------------------------------------------------------------
def fold(findings, results, cfg, sd, now, host, persist=True, complete=True):
    """The incident part of a tick. `findings` are the tick's findings (durations applied), `results` the checks' results. -> the
    `incidents` block of doctor.json: the rules that ran with their states, the rows (open and unknown), what was appended, and problems.
    `complete` says every incident rule was selected: only then is the snapshot rewritten, so a run of a subset leaves it whole."""
    ic = cfg["doctor"]["incidents"]
    rules = [dict({"check": r["check"], "state": r["state"], "detail": _clip(r["detail"], 200)}, **({"info": [_clip(i, 240) for i in r["info"]]} if r.get("info") else {}))
             for r in results if r.get("family") == FAMILY]
    out = {"enabled": bool(ic["enabled"]), "rules": rules, "rows": [], "open": 0, "unknown": 0, "appended": [], "already": [], "omitted": 0,
           "ledger": None, "snapshot": None, "problems": [], "persisted": bool(persist)}
    if not ic["enabled"]:
        return out
    live = [f for f in findings if not f["pending"] and f.get("card")]
    rows = [row_of(f, now, host) for f in live]
    rows.sort(key=lambda r: ({"critical": 0, "warning": 1, "unknown": 2}.get(r["severity"], 3), r["since"], r["id"]))
    out["omitted"] = max(0, len(rows) - ic["max_rows"])
    rows = rows[: ic["max_rows"]]
    out["rows"] = rows
    out["open"] = sum(1 for r in rows if r["status"] == "open")
    out["unknown"] = sum(1 for r in rows if r["status"] == "unknown")
    p = paths(sd)
    if persist and rules:
        new, old, problems = append(sd, rows, ic["tail_kb"] * 1024, ic["ledger_max_kb"] * 1024)
        out["appended"], out["already"], out["problems"] = new, old, problems
        out["ledger"] = p["ledger"] if os.path.exists(p["ledger"]) else None
        if not complete:
            return out
        snap = {"schema": SNAPSHOT_SCHEMA, "schema_version": SCHEMA_VERSION, "written_at": _iso(now), "at_epoch": int(now), "host": host,
                "tool_version": VERSION, "mode": "advice-only", "ledger": p["ledger"], "open": out["open"], "unknown": out["unknown"],
                "omitted": out["omitted"], "rules": rules, "rows": rows, "appended": new,
                "in_ledger": sorted(set(new) | set(old))}
        try:
            _write_atomic(p["snapshot"], json.dumps(snap, indent=1, sort_keys=True) + "\n")
            out["snapshot"] = p["snapshot"]
        except OSError as exc:
            out["problems"].append("%s: %s" % (p["snapshot"], exc))
    elif not persist:
        known = _known_ids(p["ledger"], ic["tail_kb"] * 1024, sd) or set()
        out["appended"] = []
        out["already"] = [r["id"] for r in rows if r["id"] in known]
    return out


# ---- plain words ---------------------------------------------------------------------------------------------------------------------
def risk_text(r):
    w = r["work_at_risk"]
    return "%s%s" % (w["state"], ": %s" % w["why"] if w.get("why") else "")


def lines(r, indent="  ", width=160):
    """The card in plain lines, most important first. What a supervisor reads at wake time and a person reads under a tile."""
    head = "%s[%s] %s · %s · since %s (%s ago)%s" % (indent, r["kind"], r["subject"], r["status"] if r["status"] == "unknown" else r["severity"], _hm(r["since"]),
                                                   _ago(r["read_at"] - r["since"]), "" if not r.get("since_basis") else " · " + _clip(r["since_basis"], 70))
    ev = "; ".join("%s%s: %s" % (e["record"], " (%s old)" % _ago(e["age_s"]) if e["age_s"] is not None else " (age unknown)", _clip(e["fact"], 90)) for e in r["evidence"][:4])
    out = [_clip(head, width + 40), "%s    what: %s" % (indent, _clip(r["what"], width)),
           "%s    blocking: %s" % (indent, _clip(r["blocking"]["text"], width) if r["blocking"]["text"] else "unknown (%s)" % _clip(r["blocking"]["unknown"], 100)),
           "%s    work at risk: %s" % (indent, _clip(risk_text(r), width)),
           "%s    evidence: %s%s" % (indent, _clip(ev, width + 60) if ev else "none read", " (+%d more in incidents.json)" % (len(r["evidence"]) - 4) if len(r["evidence"]) > 4 else ""),
           "%s    next: %s" % (indent, _clip(r["next_step"], width + 40))]
    if r.get("unknown"):
        out.insert(2, "%s    unknown: %s" % (indent, _clip(r["unknown"], width)))
    return out


def block(inc, indent="  "):
    """The `incidents` block of advice.txt and of `check`: a header and one card per row, or an honest line saying none were read."""
    if not inc or not inc.get("enabled"):
        return ["incidents: off (doctor.incidents.enabled is false)"]
    if inc.get("not_read"):
        return ["incidents: not read: %s" % inc["not_read"]]
    if not inc["rules"] and not inc["rows"]:
        return ["incidents: none read; this home's adapter offers no incident rule"]
    ran = [r for r in inc["rules"] if r["state"] in ("ok", "finding")]
    skipped = [r for r in inc["rules"] if r["state"] == "skipped"]
    head = "incidents: %d open · %d unknown · %d of %d rule(s) read%s" % (inc["open"], inc["unknown"], len(ran), len(inc["rules"]),
                                                                          " · %d skipped (%s)" % (len(skipped), "; ".join("%s: %s" % (r["check"], _clip(r["detail"], 160)) for r in skipped)) if skipped else "")
    out = [head]
    for r in inc["rules"]:
        out += ["%sinfo, no incident: %s" % (indent, i) for i in r.get("info") or []]
    for r in inc["rows"]:
        out += lines(r, indent)
    if inc.get("omitted"):
        out.append("%s... and %d more not carded (doctor.incidents.max_rows)" % (indent, inc["omitted"]))
    for p in inc.get("problems") or []:
        out.append("%sledger problem: %s" % (indent, _clip(p, 200)))
    return out
