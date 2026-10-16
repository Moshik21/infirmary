"""The command-line face: one line per area with a mark, then each finding with its why and its how (`-v`: the evidence),
then what repair mode did or would do about them."""
import shlex

from infirmary.doctor import finding, unknowns

MARK = {"ok": "ok", "skipped": "skip", "unknown": "unkn", "error": "ERR "}


def _mark(r):
    if r["state"] == "finding":
        live = [f for f in r["findings"] if not f["pending"]]
        if not live:
            return "wait"
        worst = max(live, key=lambda f: finding.RANK[f["status"]])["status"]
        return {"CRITICAL": "CRIT", "WARNING": "WARN", "UNKNOWN": "unkn"}[worst]
    return MARK.get(r["state"], r["state"])


def text(doc, verbose=False):
    h = doc["host"]
    out = ["infirmary %s · host %s (%s) · mode %s · %d checks in %d ms" % (doc["tool_version"], h["id"], h["os"], doc.get("repair_mode") or doc["mode"], len(doc["checks"]), doc["cost"]["took_ms"])]
    by = {}
    for f in doc["findings"]:
        by.setdefault(f["check"], []).append(f)
    for c in doc["checks"]:
        r = dict(c, findings=by.get(c["check"], []))
        n = len(r["findings"])
        summary = c["detail"] if not n else "%d finding(s); %s" % (n, c["detail"]) if c["detail"] else "%d finding(s)" % n
        out.append("[%-4s] %2d  %-34s %s" % (_mark(r), c["catalogue"], c["title"], summary[:150]))
        for line in c.get("info") or []:
            out.append("        · info: %s" % line)
        for f in r["findings"]:
            out.append("        - %s: %s%s" % (f["subject"], f["title"], "  (watching: seen %ds, confirms after its for-duration)" % f["age_s"] if f["pending"] else ""))
            out.append("          %s" % f["why"])
            c = f.get("card")
            if c and not f["pending"]:
                risk = c["work_at_risk"]
                out.append("          incident: blocking %s · work at risk %s" % (c["blocking"] or "unknown (%s)" % (c.get("unknown") or "not established"),
                                                                                  "%s%s" % (risk["state"], " (%s)" % risk["why"] if risk.get("why") else "")))
                out.append("          next: %s" % c["next_step"])
            if verbose:
                for e in f["evidence"]:
                    out.append("          evidence · %s: %s%s" % (e["source"], e["fact"], " (%s)" % e["ref"] if e.get("ref") else ""))
            hw = finding.how_text(f)
            if hw:
                out.append("          how: %s" % hw)
            for hh in finding.all_hows(f):
                o = hh.get("dry_run_output")
                if o:
                    out.append("          dry run exit %s, %d line(s)%s" % (o["rc"], o["lines"], ":" if o["tail"] else ""))
                    out += ["            " + t for t in o["tail"]]
            if verbose and f["recommendation"]:
                out.append("          recommendation: %s" % f["recommendation"])
        if verbose and not r["findings"] and c["state"] == "error":
            out.append("        error: %s" % c["detail"])
    rep = repairs_text(doc.get("repairs"), verbose)
    if rep:
        out.append("")
        out.append(rep)
    live = [f for f in doc["findings"] if not f["pending"]]
    crit = sum(1 for f in live if f["status"] == "CRITICAL")
    warn = sum(1 for f in live if f["status"] == "WARNING")
    pend = len(doc["findings"]) - len(live)
    flags = [(hid, ", ".join(k for k in ("drain", "hung", "disk_critical") if fl.get(k))) for hid, fl in sorted(doc["flags"].items())]
    flags = [x for x in flags if x[1]]
    inc = doc.get("incidents")
    if inc and inc.get("enabled") and inc.get("rules"):
        out.append("")
        out.append("incidents: %d open, %d unknown from %d rule(s); %s%s" % (
            inc["open"], inc["unknown"], len(inc["rules"]), "%d new row(s) appended to the ledger" % len(inc["appended"]) if inc.get("persisted") else "nothing written (--no-write)",
            "; PROBLEM: " + "; ".join(inc["problems"]) if inc["problems"] else ""))
    unk = doc["unknowns"] if "unknowns" in doc else unknowns.of(doc)
    if unk:
        out.append("")
        out.append("unknown (%d; the Infirmary could not decide, which is not a pass):" % len(unk))
        for u in unk:
            out.append("  - %s" % unknowns.line(u, 200))
            out.append("    next: %s" % u["next"])
    out.append("")
    out.append("%s · %d critical, %d warning%s%s%s · exit %d" % (doc["status"], crit, warn, ", %d unknown" % len(unk) if unk else "", ", %d watching" % pend if pend else "",
                                                                "; flags: " + "; ".join("%s %s" % x for x in flags) if flags else "", doc["exit_code"]))
    return "\n".join(out)


OUTCOME = {"verified": "done", "not-cleared": "FAIL", "failed": "FAIL", "refused": "NO  ", "planned": "plan", "advised": "adv ",
           "cleared": "gone", "approved": "ok  "}


def _argv(argv):
    return " ".join(shlex.quote(a) for a in argv or [])


def repairs_text(rep, verbose=False):
    """The repairs block: one line per action outcome, then the plans that wait for a person."""
    if not rep or not (rep.get("outcomes") or rep.get("plans")):
        return ""
    out = ["repairs (mode %s):" % (rep.get("repair_mode") or rep.get("mode"))]
    for o in rep.get("outcomes") or []:
        out.append("[%s] %s %s · %s" % (OUTCOME.get(o["outcome"], o["outcome"][:4]), o["tier"], o["action"], o["subject"]))
        out.append("          %s" % o["reason"])
        if verbose and o.get("argv"):
            out.append("          command: %s%s" % (_argv(o["argv"]), " (in %s)" % o["cwd"] if o.get("cwd") else ""))
        for line in (o.get("output") or []) if verbose else []:
            out.append("            " + line)
    plans = rep.get("plans") or []
    if plans:
        out.append("plans waiting for a person:")
        for p in plans:
            out.append("  %s · %s · %s" % (p["id"], p["subject"], "runs: %s" % _argv(p["apply"]) if p["runs"] == "doctor" else "carried out by %s" % p["owner"]))
            out.append("    %s%s" % (p["approve"], "  (%s)" % p["escalated"] if p.get("escalated") else ""))
    tripped = [n for n, b in sorted((rep.get("breakers") or {}).items()) if b.get("tripped")]
    if tripped:
        out.append("circuit breakers tripped: %s (infirmary reset ACTION after a look)" % ", ".join(tripped))
    return "\n".join(out)


def plan_line(p):
    return "%-10s %s · %s · %s · targets %s · %s" % (p.get("state"), p["id"], p["action"], p["subject"], len(p.get("targets") or []),
                                                     _argv(p["apply"]) + (" (in %s)" % p["cwd"] if p.get("cwd") else ""))
