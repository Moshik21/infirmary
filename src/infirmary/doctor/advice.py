"""`infirmary advise`: the periodic, advice-only run an install schedules, and the advice it leaves for a supervisor.

One run does four things and acts on nothing:

1. the catalogue check (`check`), always in advise mode: whatever the live-repair toggle says, this run repairs nothing and
   writes no plan; it keeps doctor.json and the Infirmary's for/clear memory in the state directory, as `check` does;
2. stall-explain over every alarm the source recorded in the window (`advice.window_s`, 24 h by default), each explained as of
   its own moment, the way `--replay` scores them; and, for each lane whose newest alarm was real or undecided, once more now;
3. the judged ledger (docs/judged.md): a verdict row appended for each alarm explained, and an outcome row for each earlier verdict whose
   window has passed, from records the run already read;
4. the advice: `advice.json` (infirmary.advice 1.0), `advice.txt` (the same, for a person or a supervisor to read at wake time)
   and `card.json` (one lookout.card 1.0 card: how many alarms were explained, by which explanation, and how many are real),
   written atomically into the state directory, and the card also to `advice.card` when that names a board's drop directory.

An alarm is **explained** when its verdict is benign or the supervisor's move (queued, running-long, running-remote, thinking,
declared-wait, torn-down, waiting-on-supervisor), **real** when someone has to act on the lane (waiting-on-human, wedged, dead),
and **unknown** when the evidence did not decide. A lane is **open** when its newest alarm in the window was real or unknown and
asking again now does not explain it either.
"""
import json
import os
import tempfile
import time

from infirmary import VERSION
from infirmary.core import actions, budget, hostid, live, state
from infirmary.doctor import incidents as incmod
from infirmary.doctor.context import Ctx
from infirmary.doctor import judged

SCHEMA = "infirmary.advice"
SCHEMA_VERSION = "1.0"
REAL = ("waiting-on-human", "wedged", "dead")
DEFAULT_TOOL = "stall-explain"


def kind(verdict):
    """explained, real or unknown: what an alarm turned out to be."""
    if verdict == "unknown":
        return "unknown"
    return "real" if verdict in REAL else "explained"


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _hm(epoch):
    return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(epoch))


def _ago(s):
    s = int(max(0, s))
    return "%ds" % s if s < 90 else "%dm" % (s // 60) if s < 5400 else "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def _span(s):
    return "%dh" % (s // 3600) if s % 3600 == 0 else _ago(s)


def _clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def card_tool(cfg):
    """The card's `tool` id: stall-explain, or stall-explain-<label> so the cards of several homes can share one board."""
    label = (cfg.get("advice") or {}).get("label")
    return ("%s-%s" % (DEFAULT_TOOL, label))[:48] if label else DEFAULT_TOOL


# ---- the alarms -----------------------------------------------------------------------------------------------------------------
def explain_alarms(cfg, now, src=None, deadline=None):
    """{"available", "why", "rows", "lanes"}: every recorded alarm in the window with its verdict then, and the open lanes now.
    Past `deadline` (the run's wall budget) no further alarm is explained: they are counted in `left` (and the lanes not asked
    again now in `left_lanes`), never dropped silently and never called explained."""
    from infirmary.explain import engine, evidence, sources
    window = int((cfg.get("advice") or {}).get("window_s") or 86400)
    out = {"available": False, "why": None, "window_s": window, "rows": [], "now": {}, "judged_inputs": {}, "source": None, "left": 0, "left_lanes": 0}
    late = lambda: deadline is not None and time.time() > deadline
    try:
        src = src or sources.load(cfg)
    except sources.SourceError as exc:
        out["why"] = str(exc)
        return out
    if not hasattr(src, "recorded_alarms"):
        out["why"] = "the %s source records no alarms" % cfg.get("adapter")
        return out
    out["available"] = True
    out["source"] = src
    for a in src.recorded_alarms(since=now - window, until=now):
        if late():
            out["left"] += 1
            continue
        ev =src.collect(a["subject"], evidence.Clock(10000), trigger=a["trigger"], as_of=a["at"])
        v = engine.explain(ev, cfg, took_ms=0)
        out["rows"].append({"id": "a%03d" % a["n"], "at": a["at"], "subject": v["subject"], "window": a.get("window"),
                            "verdict": v["verdict"], "kind": kind(v["verdict"]), "qualifiers": v["qualifiers"],
                            "why": v["why"], "recommendation": v["recommendation"]})
        out["judged_inputs"]["a%03d" % a["n"]] = judged.inputs(ev, v, a.get("text"))
    newest = {}
    for r in out["rows"]:
        newest[r["subject"]] = r
    for subject, r in sorted(newest.items()):
        if r["kind"] == "explained":
            continue
        if late():
            out["left_lanes"] += 1
            continue
        lane = src.resolve(lane=subject)
        if lane is None:
            out["now"][subject] = {"verdict": "torn-down", "kind": "explained", "why": "the lane has no live record now"}
            continue
        v = engine.explain(src.collect(lane, evidence.Clock(cfg["budget_ms"])), cfg)
        out["now"][subject] = {"verdict": v["verdict"], "kind": kind(v["verdict"]), "why": v["why"]}
    return out


def summarize(al):
    rows = al["rows"]
    by = {}
    for r in rows:
        if r["kind"] == "explained":
            by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    newest = {}
    for r in rows:
        newest[r["subject"]] = r
    open_lanes = sorted(s for s, r in newest.items() if r["kind"] != "explained" and (al["now"].get(s) or {}).get("kind", "unknown") != "explained")
    return {"left_unexplained": al.get("left", 0), "left_lanes": al.get("left_lanes", 0),
            "total": len(rows), "explained": sum(1 for r in rows if r["kind"] == "explained"), "real": sum(1 for r in rows if r["kind"] == "real"),
            "unknown": sum(1 for r in rows if r["kind"] == "unknown"), "by_explanation": dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))),
            "lanes": len(newest), "open_lanes": open_lanes}


# ---- the run --------------------------------------------------------------------------------------------------------------------
def build(cfg, now=None, check=True, persist=True):
    """Run the check and the explanations; -> the advice document. Only the check's own files (doctor.json and the Infirmary's for/clear
    memory) are written here, and not even those with persist=False; the advice files are written by write()."""
    now = int(now or time.time())
    sd = state.state_dir(cfg)
    run_cfg = dict(cfg, mode="advise")     # this run never repairs, whatever the toggle says
    doc = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "tool_version": VERSION, "written_at": _iso(now), "at_epoch": now,
           "host": hostid.identity(cfg)["id"], "home": (cfg.get("adapter_config") or {}).get("home"), "adapter": cfg.get("adapter"),
           "mode": live.ADVICE_ONLY, "live_repair": live.summary(cfg), "state_dir": sd, "off": state.is_off(cfg),
           "card_tool": card_tool(cfg), "doctor": None, "alarms": None, "budget": None}
    if doc["off"]:
        return doc
    t0, cpu0 = time.time(), budget.cpu_now()
    plan = budget.plan(cfg, now, sd, Ctx(cfg).osk)
    doc["budget"] = plan
    if plan["decision"] == "skipped":      # stepping aside is a result: the row says why, and nothing was checked or explained
        _spend(doc, plan, t0, cpu0, sd, now, persist)
        return doc
    deadline = t0 + plan["wall_s"]
    if check:
        doc["doctor"] = _check(run_cfg, sd, persist)
    al = explain_alarms(run_cfg, now, deadline=deadline)
    s = summarize(al) if al["available"] else None
    jd = None
    if al["available"] and ((cfg.get("advice") or {}).get("judged") or {}).get("enabled"):
        try:
            jd = judged.run(al["source"], al, cfg, sd, now, doc["host"]) if persist else {"enabled": True, "persisted": False}
        except Exception as exc:   # the advice is worth writing when the ledger step breaks
            jd = {"enabled": True, "persisted": False, "problems": ["%s: %s" % (type(exc).__name__, exc)], "counts": None}
    lim = int((cfg.get("advice") or {}).get("list_max") or 20)
    newest = {}
    for r in al["rows"]:
        newest[r["subject"]] = r
    flagged = [dict(r, later=None if newest[r["subject"]] is r else {"id": newest[r["subject"]]["id"], "verdict": newest[r["subject"]]["verdict"]})
               for r in reversed(al["rows"]) if r["kind"] != "explained"]
    doc["alarms"] = {"available": al["available"], "why": al["why"], "window_s": al["window_s"], "since": _iso(now - al["window_s"]),
                     "summary": s, "now": al["now"], "flagged": [dict(r, now=al["now"].get(r["subject"])) for r in flagged[:lim]],
                     "flagged_more": max(0, len(flagged) - lim), "judged": jd, "rows": al["rows"]}
    doc["took_ms"] = int((time.time() - t0) * 1000)
    _spend(doc, plan, t0, cpu0, sd, now, persist)
    return doc


def _spend(doc, plan, t0, cpu0, sd, now, persist):
    """What this run cost, measured: into the document, and (unless nothing is being written) into the ledger the CPU budget reads."""
    plan["took_s"], plan["cpu_s"] = round(time.time() - t0, 2), round(budget.cpu_now() - cpu0, 2)
    al = doc.get("alarms") or {}
    plan["left_unexplained"] = ((al.get("summary") or {}).get("left_unexplained") or 0)
    plan["left_lanes"] = ((al.get("summary") or {}).get("left_lanes") or 0)
    plan["cut"] = plan["left_unexplained"] > 0 or plan["left_lanes"] > 0
    if not persist or not plan["enabled"]:
        return
    try:
        budget.record(sd, now, {"decision": plan["decision"], "cpu_s": plan["cpu_s"], "wall_s": plan["took_s"]})
    except OSError as exc:
        plan["notes"].append("the cost ledger could not be written: %s" % exc)


def _check(cfg, sd, persist=True):
    from infirmary.doctor import repair, runner
    from infirmary.doctor.context import Ctx
    try:
        loaded = runner.load(cfg)
        d = runner.run(cfg, persist=persist, loaded=loaded)
        ctx = Ctx(cfg)
        ctx.adapter = loaded[0]
        d["repairs"] = repair.Engine(cfg, loaded, sd, ctx, lambda ids: runner.redetect(cfg, loaded, ids)).tick(d)
        d["cost"]["repairs_ms"] = 0
        if persist:
            runner.write_doctor_json(d, cfg)
    except Exception as exc:   # the alarms are still worth explaining when a check breaks
        return {"status": "UNKNOWN", "error": "%s: %s" % (type(exc).__name__, exc), "findings": [], "incidents": None, "unknowns": []}
    live_f = [f for f in d["findings"] if not f["pending"]]
    return {"status": d["status"], "checked_at": d["checked_at"], "checks": len(d["checks"]), "took_ms": d["cost"]["took_ms"],
            "pending": sum(1 for f in d["findings"] if f["pending"]),
            "findings": [{"id": f["id"], "status": f["status"], "title": f["title"], "recommendation": f["recommendation"]} for f in live_f],
            "incidents": d.get("incidents"), "unknowns": d["unknowns"]}


# ---- what it says ---------------------------------------------------------------------------------------------------------------
def _expl_text(by, n):
    parts = ["%s %d" % (k, v) for k, v in list(by.items())[:n]]
    return ", ".join(parts) + (", …" if len(by) > n else "")


def card(doc, stale_after_s):
    """One lookout.card 1.0 card for the advice document."""
    a = doc.get("alarms") or {}
    s = a.get("summary")
    hours = _span(a["window_s"]) if a.get("window_s") else "window"
    numbers = []
    bud = doc.get("budget") or {}
    if doc["off"]:
        status, headline = "unknown", "off (kill switch); no alarms explained"
    elif bud.get("decision") == "skipped":
        status, headline = "unknown", "skipped on purpose: %s; nothing was checked or explained this run" % bud["reason"]
        numbers = [{"label": "runs skipped", "value": bud["backed_off_runs"] + 1}]
    elif not s:
        status, headline = "unknown", _clip("alarms not read: %s" % (a.get("why") or "no source"), 160)
    else:
        status = "warn" if s["open_lanes"] else "ok"
        headline = "%s: %d of %d alarms explained" % (hours, s["explained"], s["total"])
        if s["by_explanation"]:
            headline += " (%s)" % _expl_text(s["by_explanation"], 3)
        headline += "; %d real, %d unknown; %d lane%s open now" % (s["real"], s["unknown"], len(s["open_lanes"]), "" if len(s["open_lanes"]) == 1 else "s")
        numbers = [{"label": "alarms", "value": s["total"]}, {"label": "explained", "value": s["explained"]},
                   {"label": "real", "value": s["real"]}, {"label": "unknown", "value": s["unknown"]},
                   {"label": "open now", "value": len(s["open_lanes"])}]
        if s["by_explanation"]:
            k, v = next(iter(s["by_explanation"].items()))
            numbers.append({"label": "top explanation", "value": ("%s %d" % (k, v))[:32]})
    inc = (doc.get("doctor") or {}).get("incidents")
    if inc and inc.get("enabled") and not doc["off"] and (inc["open"] or inc["unknown"]):
        n = inc["open"] + inc["unknown"]
        kinds = sorted(set(r["kind"] for r in inc["rows"]))
        status = "bad" if any(r["severity"] == "critical" for r in inc["rows"]) else "warn"
        headline = _clip("%d incident%s open (%s); %s" % (n, "" if n == 1 else "s", ", ".join(kinds[:3]) + (", …" if len(kinds) > 3 else ""), headline), 160)
        numbers = ([{"label": "incidents open", "value": n}] + numbers)[:6]
    d = doc.get("doctor") or {}
    unk = d.get("unknowns") or []
    if d.get("error"):      # the check itself broke: that is an unknown too, with its error, and the tile must not look current and fine
        unk = [{"check": "doctor", "class": "check-broke", "reason": d["error"]}] + unk
    if unk and not doc["off"]:
        if status == "ok":
            status = "unknown"
        headline = _clip("doctor: %d unknown, first %s: %s; %s" % (len(unk), unk[0]["check"], _clip(unk[0]["reason"], 60), headline), 160)
        numbers = ([{"label": "unknown checks", "value": len(unk)}] + numbers)[:6]
    if bud.get("cut") and s and not doc["off"]:      # said first: a partial run must not read as a whole one
        status = "unknown" if status == "ok" else status
        headline = "cut short on purpose (wall budget %s s): %d alarm(s) and %d lane(s) not explained; %s" % (bud["wall_s"], s["left_unexplained"], s["left_lanes"], headline)
        numbers = ([{"label": "alarms not explained", "value": s["left_unexplained"]}, {"label": "lanes not explained", "value": s["left_lanes"]}] + numbers)[:6]
    title = "Alarms explained" + (" · %s" % doc["card_tool"][len(DEFAULT_TOOL) + 1:] if doc["card_tool"] != DEFAULT_TOOL else "")
    return {"schema": "lookout.card", "schema_version": "1.0", "tool": doc["card_tool"], "title": title[:60], "status": status,
            "headline": _clip(headline, 160), "numbers": numbers, "updated_at": doc["written_at"], "mode": live.ADVICE_ONLY,
            "stale_after_s": int(min(max(stale_after_s, 60), 604800))}


def text(doc):
    """advice.txt: what a supervisor reads at wake time. Plain lines, newest and most urgent first."""
    out = ["infirmary advice · %s · %s · advice-only: nothing is repaired" % (doc["written_at"], doc["host"])]
    if doc.get("home"):
        out.append("home: %s" % doc["home"])
    if doc["off"]:
        out.append("the kill switch is on: nothing was checked or explained")
        return "\n".join(out) + "\n"
    bud = doc.get("budget") or {}
    if bud.get("decision") == "skipped":
        out.append("skipped on purpose: %s; nothing was checked or explained this run, and that is not a pass" % bud["reason"])
        out.append("the next scheduled run measures again and goes ahead when the machine is within its limits (%d run(s) skipped in a row, including this one)" % (bud["backed_off_runs"] + 1))
        return "\n".join(out) + "\n"
    if bud.get("decision") == "shortened":
        out.append("shortened on purpose: %s" % bud["reason"])
    if bud.get("backed_off_runs"):
        out.append("back after %d skipped run(s) since %s: the machine is within its limits again" % (bud["backed_off_runs"], bud["backed_off_since"]))
    for note in bud.get("notes") or []:
        out.append("budget: %s" % note)
    inc = (doc.get("doctor") or {}).get("incidents")
    if inc is not None:
        out += incmod.block(inc)
    a = doc["alarms"]
    s = a["summary"]
    if not s:
        out.append("alarms: not read (%s)" % a["why"])
    else:
        out.append("alarms in the last %s: %d · explained %d · real %d · unknown %d · lanes %d · open now %d" % (
            _span(a["window_s"]), s["total"], s["explained"], s["real"], s["unknown"], s["lanes"], len(s["open_lanes"])))
        if s["by_explanation"]:
            out.append("explained by: %s" % _expl_text(s["by_explanation"], 12))
        if bud.get("cut"):
            out.append("cut short on purpose: the wall budget of %s s was used, so %d alarm(s) and %d lane(s) asked again now were not explained (not a pass; the next run takes them)" % (
                bud["wall_s"], s["left_unexplained"], s["left_lanes"]))
        if s["open_lanes"]:
            out.append("open now (the newest alarm was real or unknown, and asking again now does not explain it):")
            newest = {}
            for r in a["rows"]:
                newest[r["subject"]] = r
            for lane in s["open_lanes"]:
                r, nv = newest[lane], a["now"].get(lane) or {}
                out.append("  - %s: %s at %s: %s | now %s: %s | %s" % (lane, r["verdict"], _hm(r["at"]), _clip(r["why"], 90),
                                                                     nv.get("verdict", "?"), _clip(nv.get("why"), 70), r["recommendation"]))
        else:
            out.append("open now: none")
        jd = a.get("judged")
        if jd and jd.get("counts"):
            out.append("judged, last %d days: %s (`infirmary judged`)" % (judged.DEFAULT_DAYS, judged.counts_line(jd["counts"])))
        elif jd and jd.get("problems"):
            out.append("judged: not recorded (%s)" % _clip(jd["problems"][0], 120))
        if a["flagged"]:
            out.append("real or unknown alarms in the window, newest first%s:" % (" (%d more in advice.json)" % a["flagged_more"] if a["flagged_more"] else ""))
            for r in a["flagged"]:
                nv = r.get("now") or {}
                after = " | now %s" % nv["verdict"] if nv else " | later %s (%s)" % (r["later"]["verdict"], r["later"]["id"]) if r.get("later") else ""
                out.append("  - %s %s %s %s%s: %s%s" % (r["id"], _hm(r["at"]), r["subject"], r["verdict"], "".join(" [%s]" % q for q in r["qualifiers"]),
                                                      _clip(r["why"], 90), after))
    d = doc.get("doctor")
    if d:
        if d.get("error"):
            out.append("doctor: the check failed (%s)" % d["error"])
        else:
            crit = sum(1 for f in d["findings"] if f["status"] == "CRITICAL")
            out.append("doctor: %s · %d finding(s), %d critical, %d still watching · %d checks in %d ms" % (
                d["status"], len(d["findings"]), crit, d["pending"], d["checks"], d["took_ms"]))
            for f in d["findings"]:
                out.append("  - [%s] %s: %s → %s" % (f["status"], f["id"], _clip(f["title"], 80), _clip(f["recommendation"], 120)))
            for u in d.get("unknowns") or []:
                out.append("  - [UNKNOWN, not a pass] %s · %s: %s → %s" % (u["check"], u["class"], _clip(u["reason"], 160), _clip(u["next"], 120)))
    return "\n".join(out) + "\n"


def _write(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".advice.", suffix=".tmp")
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
    return path


def paths(cfg):
    sd = state.state_dir(cfg)
    return {"json": os.path.join(sd, "advice.json"), "text": os.path.join(sd, "advice.txt"), "card": os.path.join(sd, "card.json")}


def write(doc, cfg, stale_after_s):
    """Write advice.json, advice.txt and card.json (and the card to `advice.card` when set). -> [files written], [problems]."""
    from infirmary.doctor import card as cardmod
    p = paths(cfg)
    c = card(doc, stale_after_s)
    wrote, problems = [], []
    for path, data in ((p["json"], json.dumps(doc, indent=1, sort_keys=True) + "\n"), (p["text"], text(doc)),
                       (p["card"], json.dumps(c, indent=2, sort_keys=True) + "\n")):
        try:
            wrote.append(_write(path, data))
        except OSError as exc:
            problems.append("%s: %s" % (path, exc))
    extra = (cfg.get("advice") or {}).get("card")
    if extra:
        dest = os.path.join(os.path.expanduser(extra), c["tool"] + ".json") if os.path.isdir(os.path.expanduser(extra)) else os.path.expanduser(extra)
        try:
            wrote.append(cardmod.write(dest, c))
        except OSError as exc:
            problems.append("card %s: %s" % (dest, exc))
    s = (doc.get("alarms") or {}).get("summary") or {}
    actions.record(state.state_dir(cfg), "infirmary", "advise", "advise", alarms=s.get("total"), explained=s.get("explained"), real=s.get("real"),
                   unknown=s.get("unknown"), open_lanes=s.get("open_lanes"), doctor=(doc.get("doctor") or {}).get("status"), off=doc["off"],
                   took_ms=doc.get("took_ms"), budget=(doc.get("budget") or {}).get("decision"), budget_reason=(doc.get("budget") or {}).get("reason"),
                   cpu_s=(doc.get("budget") or {}).get("cpu_s"), problems=problems or None)
    return wrote, problems


def stale_after(cfg):
    """How long the card may be called current: three advice intervals, at least 15 minutes."""
    return max(900, 3 * int((cfg.get("advice") or {}).get("every_s") or 600))
