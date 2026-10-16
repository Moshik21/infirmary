"""Catalogue 10: swap and memory exhaustion, and servers left behind by jobs. Reads the host beat when it is
fresh and otherwise one direct read of the kernel's own numbers. Nothing here stops a process."""
import hashlib
import os

from infirmary.doctor import finding, registry


def _pct(v):
    return "n/a" if v is None else "%.0f%%" % v


def grade(m, th):
    """('CRITICAL'|'WARNING'|None, [reasons]) from one memory reading. Swap use alone is not an alarm: a full swap with
    memory to spare is cold pages, so swap counts only together with scarce memory or a memory stall."""
    why, crit, warn = [], False, False
    avail, swap, full = m.get("avail_pct"), m.get("swap_used_pct"), m.get("psi_full")
    scarce = (avail is not None and avail <= th["avail_warn_pct"]) or (full is not None and full >= th["psi_full_warn"])
    if avail is not None and avail <= th["avail_crit_pct"]:
        crit = True
        why.append("only %s of memory is available" % _pct(avail))
    elif avail is not None and avail <= th["avail_warn_pct"]:
        warn = True
        why.append("%s of memory is available" % _pct(avail))
    if full is not None and full >= th["psi_full_crit"]:
        crit = True
        why.append("all tasks stalled on memory %.0f%% of the last minute" % full)
    elif full is not None and full >= th["psi_full_warn"]:
        warn = True
        why.append("tasks stalled on memory %.0f%% of the last minute" % full)
    if swap is not None and swap >= th["swap_crit_pct"] and scarce:
        crit = True
        why.append("swap is %s used" % _pct(swap))
    elif swap is not None and swap >= th["swap_warn_pct"] and scarce:
        warn = True
        why.append("swap is %s used" % _pct(swap))
    if m.get("native_pressure") == "critical":
        crit = True
        why.append("the OS reports critical memory pressure")
    elif m.get("native_pressure") == "warn":
        warn = True
        why.append("the OS reports memory pressure")
    return ("CRITICAL" if crit else "WARNING" if warn else None), why


def check_memory(ctx):
    th = ctx.d["memory"]
    m = ctx.memory()
    b = ctx.beat()
    beat_fresh = b is not None and b["age_s"] <= th["beat_fresh_s"]
    if m is None and not beat_fresh:
        return registry.unknown("no memory reading on this OS (no /proc/meminfo and no vm_stat)")
    status, why = grade(m, th) if m else (None, [])
    ev = []
    if m:
        ev.append(finding.fact(m["source"], "available %s of %.1f GB, swap %s of %.1f GB used, memory stall %s" % (
            _pct(m["avail_pct"]), (m["total_kb"] or 0) / 1048576.0, _pct(m["swap_used_pct"]), (m["swap_total_kb"] or 0) / 1048576.0,
            "n/a" if m["psi_full"] is None else "%.1f%%" % m["psi_full"])))
    if beat_fresh:
        ev.append(finding.fact("lookout-beat", "level %s, %ds old%s" % (b["level"], b["age_s"], ": " + "; ".join(b["why"]) if b["why"] else "")))
        if b["level"] == "critical":
            status = "CRITICAL"
            why = why or list(b["why"]) or ["the host beat is at level critical"]
        elif b["level"] == "warn" and status is None:
            status = None
    detail = "%s; beat %s" % ("; ".join(ev[0]["fact"].split("; ")[:1]) if ev else "no reading", "fresh (%s)" % b["level"] if beat_fresh else "absent or stale")
    if not status:
        return registry.ok(ev[0]["fact"] if ev else detail)
    f = finding.make(
        "memory-swap", 10, "memory-pressure", ctx.host_id, status, ctx.host_id, ctx.host_id,
        "memory and swap are %s on %s" % ("exhausted" if status == "CRITICAL" else "under pressure", ctx.host_id),
        "; ".join(why), evidence=ev,
        how=finding.how("host beat (lookout-beat) and the OS", "No owner script repairs this. Hold new lanes on this host and let the running ones finish; "
                        "the Infirmary sets the advisory drain flag in doctor.json and stops nothing."),
        recommendation="Stop starting new lanes on this host until memory recovers; if it does not, a person decides which jobs to stop.",
        lk_kind="capacity", retires_with=[])
    return registry.found([f], "; ".join(why))


def _basename(arg):
    return os.path.basename(arg.rstrip("/\\"))


def check_orphans(ctx):
    cfg = ctx.d["memory"]["orphans"]
    names = set(cfg["names"])
    if not names:
        return registry.skip("no server names configured (doctor.memory.orphans.names)")
    if not ctx.have_processes():
        return registry.unknown("no process table on this OS (no /proc and no ps)")
    procs = ctx.my_processes()
    alive = set(p["pid"] for p in ctx.processes())
    findings, n = [], 0
    rows = []
    for p in procs:
        if not p["argv"] or _basename(p["argv"][0]) not in names:
            continue
        n += 1
        orphan = p["ppid"] in (0, 1) or p["ppid"] not in alive
        age = ctx.now - p["start"] if p["start"] else None
        rss_mb = (p["rss_kb"] or 0) / 1024.0
        if orphan and age is not None and age >= cfg["min_age_s"] and rss_mb >= cfg["min_rss_mb"]:
            rows.append((p, age, rss_mb))
    if not rows:
        return registry.ok("%d matching server(s) running, none orphaned older than %d min" % (n, cfg["min_age_s"] // 60))
    total = sum(r[2] for r in rows)
    ev = []
    for p, age, rss in rows[:8]:
        ev.append(finding.fact("process table", "pid %d, started %s, %d min old, %.0f MB, parent is init, command %s (identity %s)" % (
            p["pid"], finding.now_iso(p["start"]), age // 60, rss, p["argv"][0], hashlib.sha1(p["cmdline"].encode("utf-8")).hexdigest()[:10])))
    findings.append(finding.make(
        "orphan-servers", 10, "orphan-servers", ctx.host_id, "WARNING", ctx.host_id, ctx.host_id,
        "%d orphaned server(s) hold %.1f GB" % (len(rows), total / 1024.0),
        "%s left running without a parent for at least %d min; they hold %.1f GB of memory between them" % (
            ", ".join(sorted(set(_basename(r[0]["argv"][0]) for r in rows))), cfg["min_age_s"] // 60, total / 1024.0),
        evidence=ev, how=finding.how("the job that started them", "Whether a job still needs one is the owner's to say; stopping any of them is a person's decision "
                                     "after the pid, start time and command are re-checked together."),
        recommendation="Check that no live job uses these servers, then stop them by identity (pid, start time and command), never by name pattern.",
        lk_kind="capacity"))
    return registry.found(findings, "%d orphaned" % len(rows))


SPECS = [registry.Spec("memory-swap", 10, "Memory and swap", check_memory),
         registry.Spec("orphan-servers", 10, "Orphaned servers", check_orphans)]
