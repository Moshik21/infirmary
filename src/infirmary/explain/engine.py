"""The verdict engine: evidence in, one verdict out. A pure function, no I/O, no clock of its own.

Rules run in a fixed order and the first that has POSITIVE evidence wins. Absence of evidence is never
benign: `wedged`, `torn-down` and every benign verdict need something that was seen, and a source that
failed or ran out of time stops the engine from concluding anything from silence. See docs/verdicts.md.
"""
from infirmary.core import declarations, findings


def age(seconds):
    s = int(max(0, seconds))
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def _clip(text, n=90):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _fact(source, fact, ref=None):
    row = {"source": source, "fact": fact}
    if ref:
        row["ref"] = ref
    return row


class _Result(Exception):
    pass


def explain(ev, cfg, took_ms=0):
    """Return a verdict dict (core.findings.make) for the evidence dict `ev`."""
    th = cfg["thresholds"]
    now = ev.get("as_of") or 0
    subject = ev["subject"]["id"]
    trig = ev.get("trigger") or {}
    lane = ev.get("lane") or {"known": True, "closed": False}
    agent = ev.get("agent") or {}
    act = ev.get("activity") or {}
    pipe = ev.get("pipeline") or {}
    jobs = ev.get("jobs") or []
    hosts = ev.get("hosts") or {}
    hist = ev.get("history") or {}
    rows = ev.get("declarations") or []
    failed = [s for s in ev.get("sources", []) if s.get("status") not in ("ok", None)]
    qual = []
    facts = []

    def done(verdict, why, rec="", extra_q=None):
        for q in extra_q or []:
            if q not in qual:
                qual.append(q)
        return findings.make(verdict, subject, why, facts, rec, qual, took_ms, cfg["budget_ms"],
                             checked_at=declarations.now_iso(now) if now else None)

    # 1. dead: the recorded process is positively not there
    if agent.get("alive") is False:
        facts.append(_fact(agent.get("source", "process"), "recorded agent process is gone or its identity does not match"))
        return done("dead", "no agent process at the recorded endpoint", "restart or replace the lane; nothing to wait for")

    # 2. a prompt only a person can clear
    if trig.get("kind") == "agent-blocked" or agent.get("state") == "blocked":
        src = agent.get("source") or "trigger"
        facts.append(_fact(src, "agent state is blocked on a prompt"))
        return done("waiting-on-human", "agent is blocked in front of a prompt that needs a person (%s)" % src,
                    "answer the prompt in the lane; it will not clear on its own")

    kind, row = declarations.effective(rows) if rows else (None, None)
    if row is not None:
        facts.append(_fact("declarations", "newest event: %s%s%s" % (
            kind, " %s ago" % age(now - row["at"]) if row.get("at") and now else "",
            ' "%s"' % _clip(row["text"], 60) if row.get("text") and row["text"] != "[redacted]" else "")))

    # 3. a lane with no record and nothing seen from it
    if lane.get("closed") and agent.get("state") != "busy" and not jobs:
        facts.append(_fact("lane map", lane.get("note") or "no live record for this lane"))
        return done("torn-down", "lane has no live record (%s)" % (lane.get("note") or "torn down"),
                    "nothing to wake; the alarm is moot")
    if not lane.get("known", True) and not lane.get("closed"):
        return done("unknown", lane.get("note") or "cannot tell which lane this is",
                    "look at the lane" if lane.get("note") else "name the lane or window explicitly")

    # 4. positive evidence of progress
    progress = []   # (verdict, why, qualifiers)
    notes = []
    best = None
    for job in jobs:
        cls = job.get("class") or "default"
        cap = th["job_max_s"].get(cls, th["job_max_s"]["default"])
        held = job.get("held_s")
        if job.get("kind") == "holder":
            if held is not None and held > cap:
                notes.append("%s record held %s is past the %s cap; ignored" % (cls, age(held), age(cap)))
                if "stale-record" not in qual:
                    qual.append("stale-record")
                continue
            h = hist.get(job.get("cmd_key") or "")
            limit_note = ""
            if h and held is not None:
                if h.get("n", 0) >= th["p90_min_n"]:
                    if held > th["wedge_p90_factor"] * h["p90_s"] and not (act.get("grew") or (agent.get("cpu_cores") or 0) > th["busy_cpu_cores"]):
                        notes.append("%s held %s is past %.1fx p90 (%s) with no growth" % (cls, age(held), th["wedge_p90_factor"], age(h["p90_s"])))
                        continue
                    limit_note = " (p50 %s, p90 %s, n=%d)" % (age(h["p50_s"]), age(h["p90_s"]), h["n"])
                else:
                    limit_note = " (n<%d)" % th["p90_min_n"]
            if best is None or (held or 0) > (best.get("held_s") or 0):
                best = dict(job, note=limit_note)
    for n in notes:
        facts.append(_fact("slot record", n))
    if best:
        host = best.get("host")
        cmd = _clip(best.get("cmd") or best.get("cmd_key") or "job", 48)
        facts.append(_fact(best.get("source", "slot record"), "%s slot held %s on %s: %s" % (best.get("class"), age(best.get("held_s") or 0), host or "this host", cmd)))
        if host:
            hv = hosts.get(host) or {}
            fresh = hv.get("beat_age_s") is not None and hv["beat_age_s"] <= th["beat_fresh_s"]
            canary = hv.get("canary_ok") and (hv.get("canary_age_s") is None or hv["canary_age_s"] <= th["canary_fresh_s"])
            if hv:
                facts.append(_fact("host %s" % host, "beat %s, level %s; canary %s" % (
                    "%s old" % age(hv["beat_age_s"]) if hv.get("beat_age_s") is not None else "absent", hv.get("level", "?"),
                    "ok" if canary else "not ok or stale")))
            if hv.get("level") in ("warn", "critical"):
                qual.append("slow-host")
            if fresh and canary:
                return done("running-remote", "%s job on %s for %s%s%s" % (best.get("class"), host, age(best.get("held_s") or 0), best["note"], _more(act, agent)),
                            "no action; the host is healthy")
            qual.append("host-unverified")
        return done("running-long", "%s job %s for %s%s%s" % (best.get("class"), "on " + host if host else "(%s)" % cmd, age(best.get("held_s") or 0), best["note"], _more(act, agent)),
                    "no action; recheck if it runs past its usual length")
    tickets = [j for j in jobs if j.get("kind") == "ticket"]
    if tickets:
        t = min(tickets, key=lambda j: j.get("ahead", 0))
        facts.append(_fact(t.get("source", "slot ticket"), "%s ticket, %d ahead, waiting %s" % (t.get("class"), t.get("ahead", 0), age(t.get("held_s") or 0))))
        return done("queued", "waiting for a %s slot, %d ahead, %s in line" % (t.get("class"), t.get("ahead", 0), age(t.get("held_s") or 0)), "no action; it moves when a slot frees")
    if pipe.get("state") == "round-active":
        what = "step %s%s (%s)" % (pipe.get("step"), "" if pipe.get("round") in (None, "-") else " round %s" % pipe["round"], pipe.get("trigger"))
        if pipe.get("last_activity") and now:
            when = "last progress %s ago" % age(now - pipe["last_activity"])
        elif pipe.get("since") and now:
            when = "round running %s" % age(now - pipe["since"])
        else:
            when = "age unknown"
        facts.append(_fact(pipe.get("source", "pipeline ledger"), "%s active, %s" % (what, when)))
        return done("running-long", "pipeline %s is running, %s" % (what, when), "no action")
    if pipe.get("state") == "pending":
        facts.append(_fact(pipe.get("source", "pipeline ledger"), "run pending"))
        return done("queued", "pipeline run is pending its turn", "no action")
    cpu = agent.get("cpu_cores")
    if cpu is not None and cpu >= th["busy_cpu_cores"]:
        facts.append(_fact("process tree", "%.1f cores over the last sample, %s descendants" % (cpu, agent.get("descendants", "?"))))
        return done("running-long", "agent process tree is burning %.1f cores" % cpu, "no action")
    if act.get("grew") or (act.get("newest_age_s") is not None and act["newest_age_s"] <= th["activity_fresh_s"]):
        facts.append(_fact(act.get("source", "activity"), "watched files changed %s ago" % age(act.get("newest_age_s") or 0)))
        return done("running-long", "watched files are still changing (last %s ago)" % age(act.get("newest_age_s") or 0), "no action")
    if agent.get("state") == "busy":
        src = agent.get("source") or "agent state"
        facts.append(_fact(src, "agent is in a turn (busy)"))
        attested = src in ("doorbell-deferral",)
        return done("thinking", "agent is mid-turn per %s%s" % (src, "; no activity sample to corroborate" if attested else ""),
                    "no action; a model call waiting near zero CPU is normal",
                    extra_q=["busy-attested"] if attested else None)

    # 5. an instruction nobody read, with an idle agent (or none to type to) and no progress anywhere
    if trig.get("kind") == "doorbell-undelivered":
        facts.append(_fact("trigger", "instruction unread; the watcher found the agent exited or its endpoint missing, so the doorbell was not typed"))
        if failed:
            return _incomplete(done, failed, facts)
        return done("wedged", "an instruction is unread and the watcher reports no agent to deliver it to, with no job, ticket or growth behind it",
                    "look at the lane; the agent is gone or unreachable, so recover it")
    if trig.get("kind") == "doorbell-idle":
        facts.append(_fact("trigger", "instruction unread after %s delivery attempts with the pane idle" % trig.get("attempts", "?")))
        if failed:
            return _incomplete(done, failed, facts)
        return done("wedged", "an instruction is unread after %s doorbell attempts and the agent is idle with no job, ticket or growth behind it" % trig.get("attempts", "?"),
                    "look at the lane; the doorbell is not reaching it")

    # 6. what the worker said about itself
    note = ""
    if kind in ("done", "failed", "blocked", "needs-decision", "paused", "working", "resumed"):
        stale = stale_declaration(ev, row, now)
        if stale:
            facts.append(_fact("declarations", stale))
            qual.append("stale-declaration")
            kind, note = None, "; " + stale
    if kind in ("done", "failed", "blocked", "needs-decision"):
        what = {"done": "finished", "failed": "failed", "blocked": "blocked", "needs-decision": "asked a question"}[kind]
        ago = " %s ago" % age(now - row["at"]) if row.get("at") and now else ""
        return done("waiting-on-supervisor", "%s%s; the next move is the supervisor's" % (what, ago),
                    "collect or answer it; nothing to wake the worker for")
    if kind == "paused":
        at = row.get("at")
        limit = row.get("until")
        if limit is None and at and row.get("wait_hint_s"):
            limit = at + row["wait_hint_s"] * th["declared_wait_grace_factor"]
        if limit is not None and now and now > limit:
            facts.append(_fact("declarations", "declared wait is %s past its stated length" % age(now - limit)))
            return done("unknown", "declared wait is overdue by %s and nothing shows progress" % age(now - limit), "look at the lane")
        left = " (stated length %s, started %s ago)" % (age(row["wait_hint_s"]), age(now - at)) if row.get("wait_hint_s") and at and now else ""
        return done("declared-wait", "worker declared a wait%s: %s" % (left, _clip(row["text"], 70) if row["text"] != "[redacted]" else "reason redacted"),
                    "no action; it re-surfaces when the declared time is up")

    # 7. silence with nothing to explain it
    idle = trig.get("idle_s") if trig.get("idle_s") is not None else agent.get("idle_s")
    if idle is not None:
        floor = th["wedge_min_s"]
        if idle >= floor and not failed:
            facts.append(_fact("trigger", "idle %s with no job, ticket, growth, declaration or pipeline activity" % age(idle)))
            return done("wedged", "idle %s, nothing running or declared" % age(idle), "look at the lane")
        if failed and idle >= floor:
            return _incomplete(done, failed, facts)
        facts.append(_fact("trigger", "idle %s, below the %s wedge floor" % (age(idle), age(floor))))
        why = "quiet %s, below the %s wedge floor, and no job, ticket, growth or declaration explains it%s%s" % (
            age(idle), age(floor), " (declared %s)" % kind if kind in ("working", "resumed") else "", note)
        return done("unknown", why, "recheck in %s; act only if it is still quiet" % age(floor - idle if floor > idle else 60))
    if failed:
        return _incomplete(done, failed, facts)
    return done("unknown", "no evidence either way" + note, "look at the lane")


def stale_declaration(ev, row, now):
    """Why the newest declaration cannot count, or None. A declaration written before the machine's last boot, or
    before the lane's newest agent session began, says nothing about the agent that is there now: the boot or
    the relaunch stopped the one that declared it. A source that supplies `host` or `lane.session_at` vouches for
    the rule; when it cannot say the time (None), the declaration cannot be shown to come after it and does not
    count. Evidence that carries neither key predates the rule and is judged as recorded."""
    marks = []
    if ev.get("host") is not None:
        marks.append(("machine's last boot", ev["host"].get("boot_at")))
    lane = ev.get("lane") or {}
    if "session_at" in lane:
        marks.append(("lane's newest agent session", lane.get("session_at")))
    at = row.get("at")
    for what, t in marks:
        if t is None:
            return "declaration (%s) cannot be shown to come after the %s, whose time is unknown" % (row.get("state"), what)
        if at is None or at <= t:
            return "declaration (%s) predates the %s%s; it says nothing about the agent now" % (row.get("state"), what, " by %s" % age(t - at) if at is not None and now else "")
    return None


def _more(act, agent):
    bits = []
    if agent.get("cpu_cores"):
        bits.append("%.1f cores" % agent["cpu_cores"])
    if act.get("grew"):
        bits.append("log growing")
    return " · " + " · ".join(bits) if bits else ""


def _incomplete(done, failed, facts):
    names = ", ".join("%s %s%s" % (s["name"], s["status"], ": " + s["note"] if s["status"] == "unavailable" and s.get("note") else "") for s in failed)   # an unreadable source says why
    facts.append({"source": "collectors", "fact": names})
    return done("unknown", "evidence incomplete (%s); silence alone proves nothing" % names, "run again, or look at the lane")
