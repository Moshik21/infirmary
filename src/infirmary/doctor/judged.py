"""Judged alarms: for every stall alarm the Infirmary explains, the verdict it gave, what the lane did next, and whether the verdict was wrong
(docs/judged.md).

Two kinds of row go to `<state>/judged.jsonl`, in the family of the value ledger (docs/ledger-spec.md of the dashboard repository: `v`, `id`,
`tool`, `host`, `read_at`, `reader`, `supersedes`; an unknown is written as unknown with its reason):

    verdict   appended once per alarm, when `infirmary advise` first explains it: the verdict, the sources it rests on, and how old
              each state source was at the alarm's moment;
    outcome   appended once per verdict, once its window has passed, from records the source already reads: what the lane did next
              (relaunched, gone, finished, answered, moved, stuck, quiet) and the judgement the fixed table below gives.

Append-only: a row is never edited or removed, an outcome is a NEW row that names its verdict (`of`), and nothing acts on a row. The judgement is
a pure function of (verdict, outcome, whether an instruction reached the lane); the table below is the whole rule, and docs/judged.md prints
the same table. Anything not yet decidable, or lacking evidence, is `undecided` with its reason and is never counted as agreeing.

It reads what the advise run already read (the alarm's evidence, the lane's records and the fleet ledger through the source, which keeps what
it read in memory) and appends a few short rows: no process is added.
"""
import hashlib
import json
import os
import re
import time

from infirmary import VERSION

SCHEMA = "infirmary.judged"
LEDGER = "judged.jsonl"
DEFAULT_DAYS = 14

# what each verdict claims about the lane
CLAIM = {"queued": "moving", "running-long": "moving", "running-remote": "moving", "thinking": "moving", "declared-wait": "moving",
         "waiting-on-supervisor": "owed", "torn-down": "gone",
         "waiting-on-human": "human", "wedged": "real", "dead": "real", "unknown": None}
KIND = {"moving": "explained", "owed": "explained", "gone": "explained", "human": "real", "real": "real", None: "unknown"}

# what the lane did next, strongest first (a replay file's hand label may also say `healthy`): the strongest record inside the window names the outcome
STRENGTH = ("relaunched", "gone", "finished", "answered", "moved")

AGREED, WRONG_BENIGN, WRONG_REAL, UNDECIDED = "agreed", "wrong-benign", "wrong-real", "undecided"
JUDGEMENTS = (AGREED, WRONG_BENIGN, WRONG_REAL, UNDECIDED)


def key(judgement):
    """The name a count goes by in JSON: wrong-benign is `wrong_benign`."""
    return judgement.replace("-", "_")



# (claim, outcome) -> (judgement, why). A cell that is not here is undecided with NO_RULE. `real` + finished/answered/moved is handled in judge().
_RELAUNCH_MAYBE_OWN = "a relaunch after a supervisor-owed verdict may be the supervisor's own next move"
_PERSON = "a person answering at the pane leaves no record, so the lane moving on does not show it cleared by itself"
TABLE = {
    ("moving", "relaunched"): (WRONG_BENIGN, "the lane needed a relaunch"),
    ("moving", "stuck"): (WRONG_BENIGN, "the stall persisted past the window"),
    ("moving", "finished"): (AGREED, "the lane went on and reported done"),
    ("moving", "answered"): (AGREED, "the lane went on after its question was answered"),
    ("moving", "moved"): (AGREED, "the lane went on: a new record of its own"),
    ("moving", "gone"): (UNDECIDED, "the lane was cleaned up inside the window; the records do not say whether it had stalled"),
    ("moving", "quiet"): (UNDECIDED, "no record from the lane inside the window and no later real alarm: silence alone does not show a stall"),
    ("owed", "finished"): (AGREED, "the lane had reported done"),
    ("owed", "answered"): (AGREED, "the lane's question was answered"),
    ("owed", "relaunched"): (UNDECIDED, _RELAUNCH_MAYBE_OWN),
    ("owed", "gone"): (UNDECIDED, "the lane was cleaned up inside the window; that may be the supervisor's own move"),
    ("owed", "moved"): (UNDECIDED, "the lane moved, and the records do not say whether the supervisor's move came first"),
    ("owed", "stuck"): (UNDECIDED, "the lane stayed quiet and a later alarm was real; the records do not say whether it was still owed"),
    ("owed", "quiet"): (UNDECIDED, "no record from the lane inside the window: silence does not show the supervisor's move was made"),
    ("gone", "quiet"): (AGREED, "nothing was recorded for the lane after the alarm"),
    ("gone", "relaunched"): (UNDECIDED, "the lane was dispatched again; the records cannot tell a lane that was still live from a new task of the same name"),
    ("gone", "gone"): (UNDECIDED, "the lane was cleaned up inside the window, after a verdict that said it already was"),
    ("gone", "finished"): (UNDECIDED, "the lane wrote records after a verdict that said it was gone"),
    ("gone", "answered"): (UNDECIDED, "the lane wrote records after a verdict that said it was gone"),
    ("gone", "moved"): (UNDECIDED, "the lane wrote records after a verdict that said it was gone"),
    ("gone", "stuck"): (UNDECIDED, "a later real alarm named the lane after a verdict that said it was gone"),
    ("human", "relaunched"): (AGREED, "someone relaunched the lane"),
    ("human", "stuck"): (AGREED, "the lane stayed stuck and a later alarm was real"),
    ("human", "finished"): (UNDECIDED, _PERSON),
    ("human", "answered"): (UNDECIDED, _PERSON),
    ("human", "moved"): (UNDECIDED, _PERSON),
    ("human", "gone"): (UNDECIDED, "the lane was cleaned up inside the window; the records do not say whether it was for this stall"),
    ("human", "quiet"): (UNDECIDED, "no record from the lane inside the window and no later real alarm"),
    ("real", "relaunched"): (AGREED, "someone relaunched the lane"),
    ("real", "stuck"): (AGREED, "the lane stayed stuck and a later alarm was real"),
    ("real", "gone"): (UNDECIDED, "the lane was cleaned up inside the window; the records do not say whether it was for this stall"),
    ("real", "quiet"): (UNDECIDED, "no record from the lane inside the window and no later real alarm"),
    # `healthy` is only ever a hand label in a replay file (`--replay`): the lane was in the state the verdict names and nothing needed doing
    ("moving", "healthy"): (AGREED, "the hand label calls the lane healthy"),
    ("human", "healthy"): (WRONG_REAL, "the hand label calls the lane healthy"),
    ("real", "healthy"): (WRONG_REAL, "the hand label calls the lane healthy"),
}
NO_RULE = "the table has no rule for this verdict and outcome"
UNKNOWN_VERDICT = "the verdict was unknown: it claimed nothing, so there is nothing to judge"
INSTRUCTED = "an instruction reached the lane inside the window, so the records cannot show it moved on by itself"
BY_ITSELF = "the lane moved on inside the window with no relaunch and no instruction recorded (a person's act at the pane leaves no record)"


def judge(verdict, outcome, instructed=False):
    """(judgement, why) for a verdict and what the lane did next. The fixed table of docs/judged.md."""
    claim = CLAIM.get(verdict, "?")
    if claim is None:
        return UNDECIDED, UNKNOWN_VERDICT
    if claim == "?":
        return UNDECIDED, NO_RULE
    if claim == "real" and outcome in ("finished", "answered", "moved"):
        return (UNDECIDED, INSTRUCTED) if instructed else (WRONG_REAL, BY_ITSELF)
    return TABLE.get((claim, outcome), (UNDECIDED, NO_RULE))


def classify(events):
    """The outcome the lane's events name: the strongest kind present, else `quiet` (nothing recorded)."""
    kinds = set(e["kind"] for e in events)
    for k in STRENGTH:
        if k in kinds:
            return k
    return "quiet"


# ---- the rows ----------------------------------------------------------------------------------------------------------------------
def paths(sd):
    return os.path.join(sd, LEDGER)


def _base(row, rid, host, now):
    return {"v": 1, "schema": SCHEMA, "id": rid, "row": row, "tool": "infirmary", "host": host,
            "read_at": int(now), "reader": "infirmary/%s" % VERSION, "supersedes": None}


def _source_name(source):
    """`busy hook (3s ago)` -> `busy hook`, `host box-a` -> `host`: the class of a source, not its instance or its age."""
    name = re.sub(r"\s*\(.*$", "", source or "").strip().lower()
    return "host" if name.startswith("host ") else name


def _age(now, at, why):
    return {"age_s": int(max(0, now - at)), "unknown": None} if at is not None else {"age_s": None, "unknown": why}


def state_ages(ev):
    """How old each of the lane's state sources was at the alarm's moment, from the evidence the verdict was made on. A source that gave
    no time is `age_s: null` with its reason, never 0."""
    now = ev.get("as_of") or 0
    decl = [d["at"] for d in ev.get("declarations") or [] if d.get("at") is not None]
    jobs = [j.get("started") for j in ev.get("jobs") or [] if j.get("kind") == "holder" and j.get("started") is not None]
    pipe = ev.get("pipeline") or {}
    lane, host = ev.get("lane") or {}, ev.get("host") or {}
    no_time = "the record carries no time"
    return {
        "declaration": _age(now, max(decl) if decl else None, "no declaration was read for the lane"),
        "session": _age(now, lane.get("session_at"), lane.get("note") or "the lane's session start is not recorded"),
        "boot": _age(now, host.get("boot_at"), "the machine's boot time is not recorded"),
        "slot_record": _age(now, max(jobs) if jobs else None, "no slot job of the lane was in flight"),
        "pipeline": _age(now, pipe.get("last_activity") or pipe.get("since"), no_time if pipe.get("state") == "round-active" else "no pipeline round was active"),
    }


def text_hash(text):
    """The first 8 hex digits of the sha1 of an alarm's text: what names the alarm in a verdict id, without keeping the text."""
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:8]


def inputs(ev, v, text=None):
    """The per-alarm facts a verdict row keeps beyond the verdict: the sources it cites, the ages of the state sources, and the hash of the
    alarm's text (never the text)."""
    return {"evidence_class": sorted(set(_source_name(f.get("source")) for f in v.get("evidence") or [] if f.get("source"))),
            "state_ages": state_ages(ev), "text_sha1": text_hash(text)}


def verdict_id(at, text_sha1):
    """`verdict:<alarm time>#<text hash>`: from the alarm's content (its text names the window), not its position in the watcher's log nor the
    lane the lane map resolves it to today, so a log trimmed from the head or a lane cleaned up since does not give an alarm already recorded
    a second id."""
    return "verdict:%d#%s" % (int(at), text_sha1)


def verdict_row(a, extra, host, now, cfg):
    j = (cfg.get("advice") or {}).get("judged") or {}
    claim = CLAIM.get(a["verdict"])
    return dict(_base("verdict", verdict_id(a["at"], extra["text_sha1"]), host, now),
                alarm={"id": a["id"], "at": int(a["at"]), "text_sha1": extra["text_sha1"]}, subject=a["subject"], verdict=a["verdict"], kind=KIND[claim], claim=claim,
                qualifiers=list(a.get("qualifiers") or []), evidence_class=extra["evidence_class"], state_ages=extra["state_ages"],
                settle_s=int(j.get("settle_s") or 1800), patience_s=int(j.get("patience_s") or 21600))


def outcome_row(vrow, outcome, judgement, why, records, instructed, host, now):
    row = dict(_base("outcome", "outcome:" + vrow["id"], host, now), of=vrow["id"], window_s=vrow["settle_s"], outcome=outcome, judgement=judgement,
               reason=why, records=records, instructed=bool(instructed))
    return row


# ---- the ledger --------------------------------------------------------------------------------------------------------------------
def _valid(r):
    """Whether a row has the fields the follow-up and the counts read, so one hand-edited line cannot break either."""
    if r["row"] == "outcome":
        return isinstance(r.get("of"), str)
    a = r.get("alarm")
    return (isinstance(a, dict) and isinstance(a.get("at"), int) and isinstance(r.get("subject"), str) and r.get("verdict") in CLAIM
            and "claim" in r and r["claim"] == CLAIM[r["verdict"]] and r.get("kind") in KIND.values() and isinstance(r.get("settle_s"), int) and isinstance(r.get("patience_s"), int)
            and isinstance(r.get("read_at"), int))


def read(sd):
    """(rows, skipped, problem): every row that parses and carries an id, a `row` kind and our schema; the lines it skipped with their reason;
    and a problem when the file exists and cannot be read. Never raises. A torn last line is one skipped line."""
    rows, skipped = [], []
    try:
        with open(paths(sd), "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return [], [], None
    except OSError as exc:
        return [], [], "%s: %s" % (paths(sd), exc)
    seen = set()
    for n, raw in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            skipped.append({"line": n, "why": "not JSON"})
            continue
        if not isinstance(r, dict) or r.get("schema") != SCHEMA or r.get("v") != 1 or r.get("row") not in ("verdict", "outcome") or not isinstance(r.get("id"), str):
            skipped.append({"line": n, "why": "not a judged ledger row (schema %s 1)" % SCHEMA})
        elif not _valid(r):
            skipped.append({"line": n, "why": "a %s row without the fields it needs" % r["row"]})
        elif r["id"] in seen:
            skipped.append({"line": n, "why": "duplicate id %s" % r["id"]})
        else:
            seen.add(r["id"])
            rows.append(r)
    return rows, skipped, None


def append(sd, rows, max_bytes):
    """Append the rows whose id is not in the ledger yet: ([ids appended], [problems]). Append-only: the file is opened for append, never
    truncated, rotated or rewritten, and a full ledger refuses (visibly) instead of dropping old rows."""
    if not rows:
        return [], []
    p = paths(sd)
    new, problems = [], []
    try:
        os.makedirs(sd, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    except OSError as exc:
        return [], ["%s: %s" % (p, exc)]
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)     # two runs at once must not both append one id
        except (ImportError, OSError):
            pass
        known, _, problem = read(sd)
        if problem:
            return [], ["%s: the ledger cannot be read to tell which rows it already holds, so nothing was appended" % p]
        known = set(r["id"] for r in known)
        for r in rows:
            if r["id"] in known:
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
    return new, problems


# ---- the run: verdicts at explain time, outcomes once the window has passed ----------------------------------------------------------
def _outcome_of(src, vrow, until):
    """What the source's records show for the lane inside (alarm, until]: (events, instructed, unreadable reasons)."""
    got = src.outcome(vrow["subject"], vrow["alarm"]["at"], until)
    return list(got.get("events") or []), bool(got.get("instructed")), list(got.get("unreadable") or [])


def _records(events):
    return [{"record": e.get("record"), "kind": e["kind"], "at": e.get("at")} for e in sorted(events, key=lambda e: (e.get("at") or 0, e["kind"]))][:6]


def follow_up(src, rows, host, now):
    """The outcome rows due now, for the verdicts in `rows` that have none. -> ([rows], pending). Deterministic from the records: a lane is
    judged by what its records show inside (alarm, alarm + settle_s], and a lane that recorded nothing waits for a later alarm of its own
    (up to patience_s) before it can be called stuck."""
    have = set(r["of"] for r in rows if r["row"] == "outcome" and isinstance(r.get("of"), str))
    verdicts = [r for r in rows if r["row"] == "verdict"]
    out, pending = [], 0
    for v in verdicts:
        if v["id"] in have:
            continue
        at, window = v["alarm"]["at"], v["settle_s"]
        if now < at + window:
            pending += 1
            continue
        if v["claim"] is None:
            out.append(outcome_row(v, "none", *judge(v["verdict"], "none"), records=[], instructed=False, host=host, now=now))
            continue
        if not hasattr(src, "outcome"):
            out.append(outcome_row(v, "unreadable", UNDECIDED, "the source cannot say what a lane did next", [], False, host, now))
            continue
        events, instructed, unreadable = _outcome_of(src, v, at + window)
        if unreadable and not events:
            out.append(outcome_row(v, "unreadable", UNDECIDED, "the lane's records could not be read: " + "; ".join(unreadable), [], False, host, now))
            continue
        outcome = classify(events)
        if outcome == "quiet" and v["claim"] in ("moving", "human", "real"):
            later = [r for r in verdicts if r["subject"] == v["subject"] and r["kind"] == "real" and at + window <= r["alarm"]["at"] <= at + v["patience_s"]
                     and r["alarm"]["at"] <= now]
            if not later and now < at + v["patience_s"]:
                pending += 1                  # a later alarm of this lane may still come; the window for it is not over
                continue
            if later:
                until = min(r["alarm"]["at"] for r in later)
                more, instructed2, _ = _outcome_of(src, v, until)
                if more:
                    out.append(outcome_row(v, "quiet", UNDECIDED, "the lane moved after the window and before the later alarm", _records(more), instructed2, host, now))
                    continue
                outcome = "stuck"
        judgement, why = judge(v["verdict"], outcome, instructed)
        out.append(outcome_row(v, outcome, judgement, why, _records(events), instructed, host, now))
    return out, pending


def run(src, al, cfg, sd, now, host):
    """The advise run's part: append a verdict row for each explained alarm, then the outcome rows that are due. -> the `judged` block of
    advice.json (counts, the problems, the cost in ms)."""
    j = (cfg.get("advice") or {}).get("judged") or {}
    t0 = time.time()
    cap = int(j.get("ledger_max_kb") or 16384) * 1024
    rows, _, problem = read(sd)
    out = {"enabled": True, "persisted": True, "ledger": paths(sd), "verdicts_appended": 0, "outcomes_appended": 0, "pending": 0,
           "problems": [], "counts": None, "ms": 0}
    if problem:
        out["problems"].append(problem)
        return out
    have = set(r["id"] for r in rows)
    vrows = list(dict((v["id"], v) for v in reversed([verdict_row(r, al["judged_inputs"][r["id"]], host, now, cfg)
                                                       for r in al["rows"] if r["id"] in al["judged_inputs"]])).values())[::-1]
    new_v, problems = append(sd, [r for r in vrows if r["id"] not in have], cap)    # the ledger is read again under its lock, and only when there is something to add
    new_v = set(new_v)
    rows += [r for r in vrows if r["id"] in new_v]
    due, out["pending"] = follow_up(src, rows, host, now)
    new_o, more = append(sd, due, cap)
    new_o = set(new_o)
    rows += [r for r in due if r["id"] in new_o]
    out.update(verdicts_appended=len(new_v), outcomes_appended=len(new_o), problems=problems + more, counts=tally(rows, now - DEFAULT_DAYS * 86400, now, now))
    out["ms"] = int((time.time() - t0) * 1000)
    return out


# ---- the number a person reads -----------------------------------------------------------------------------------------------------
def tally(rows, since, until, now):
    """The counts for the verdicts whose alarm falls in [since, until), straight from the rows: explained, real, unknown (what the verdicts
    said), and agreed, wrong-benign, wrong-real, undecided (what the outcomes said), with each undecided reason counted. A verdict with no
    outcome row is undecided, and says why."""
    outs, byid = {}, {}
    for r in rows:
        if r["row"] == "outcome" and isinstance(r.get("of"), str):
            outs.setdefault(r["of"], r)
        elif r["row"] == "verdict":
            byid.setdefault(r["id"], r)
    vs = [r for r in byid.values() if since <= r["alarm"]["at"] < until]
    c = {"alarms": len(vs), "explained": 0, "real": 0, "unknown": 0, "agreed": 0, "wrong_benign": 0, "wrong_real": 0, "undecided": 0, "undecided_by": {}}
    for v in vs:
        c[v["kind"]] += 1
        o = outs.get(v["id"])
        if o is None:
            j, why = UNDECIDED, ("the alarm's window is still open" if now < v["alarm"]["at"] + v["settle_s"]
                                 else "no outcome recorded yet: waiting for a later alarm of the lane, or the next advise run")
        else:
            j, why = o.get("judgement"), o.get("reason")
            if j not in JUDGEMENTS:
                j, why = UNDECIDED, "the outcome row carries no judgement this reader knows"
        c[key(j)] += 1
        if j == UNDECIDED:
            c["undecided_by"][why] = c["undecided_by"].get(why, 0) + 1
    first = min((r["read_at"] for r in rows if r["row"] == "verdict"), default=None)
    c["undecided_by"] = dict(sorted(c["undecided_by"].items(), key=lambda kv: (-kv[1], kv[0])))
    c["window"] = {"since": int(since), "until": int(until)}
    c["rows_start"] = first
    c["lower_bound"] = first is not None and first > since
    return c


def _hm(epoch):
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(epoch))


def counts_line(c):
    return "explained %d · real %d · wrong-benign %d · wrong-real %d · undecided %d" % (c["explained"], c["real"], c["wrong_benign"], c["wrong_real"], c["undecided"])


def report(rows, skipped, problem, since, until, now):
    """-> the document `infirmary judged` prints: the counts, or an honest unknown with its reason."""
    doc = {"schema": "infirmary.judged.report", "schema_version": "1.0", "tool_version": VERSION, "read_at": int(now),
           "window": {"since": int(since), "until": int(until)}, "counts": None, "unknown": None, "skipped": skipped}
    if problem:
        doc["unknown"] = problem
    elif not [r for r in rows if r["row"] == "verdict"]:
        doc["unknown"] = "no verdict row in the ledger: the advise run has not explained an alarm yet, or its source records none; this is not zero"
    else:
        doc["counts"] = tally(rows, since, until, now)
    return doc


def text(doc):
    w = doc["window"]
    out = ["judged alarms · %s to %s · advice-only: nothing acts on a row" % (_hm(w["since"]), _hm(w["until"]))]
    c = doc["counts"]
    if c is None:
        out.append("unknown: %s" % doc["unknown"])
        return "\n".join(out) + "\n"
    rows = [("alarms recorded", c["alarms"], "verdicts in the window"),
            ("explained", c["explained"], "called benign or the supervisor's move"),
            ("real", c["real"], "called real"),
            ("unknown verdicts", c["unknown"], "the evidence did not decide"),
            ("agreed", c["agreed"], "what the lane did next fits the verdict"),
            ("wrong-benign", c["wrong_benign"], "a benign verdict, then a relaunch or a stall that persisted past the window"),
            ("wrong-real", c["wrong_real"], "a real verdict, then the lane moved on with no relaunch and no instruction recorded"),
            ("undecided", c["undecided"], "not yet decidable or lacking evidence (never counted as agreed)")]
    out += ["  %-17s %4d   %s" % r for r in rows]
    for why, n in list(c["undecided_by"].items())[:8]:
        out.append("      %3d  %s" % (n, why))
    if len(c["undecided_by"]) > 8:
        out.append("      ... and %d more reason(s) in --json" % (len(c["undecided_by"]) - 8))
    if c["lower_bound"]:
        out.append("the ledger starts at %s, after the window does: these are counts from then on, at least that many, not the whole window" % _hm(c["rows_start"]))
    if doc["skipped"]:
        out.append("skipped %d line(s) of the ledger: %s" % (len(doc["skipped"]), "; ".join("line %d %s" % (s["line"], s["why"]) for s in doc["skipped"][:3])))
    return "\n".join(out) + "\n"
