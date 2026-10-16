"""Run the catalogue once: collect findings, apply the for/clear durations, derive the host flags, write doctor.json.

Local checks run in turn under the tick budget; probes of other hosts run beside them in threads under their own
budget. A check that raises or overruns is reported as such (state `error`), never as healthy.
"""
import hashlib
import json
import os
import threading
import time

from infirmary import VERSION
from infirmary.core import execute, hostid, live as livemod, state
from infirmary.doctor import finding, incidents, registry, unknowns
from infirmary.doctor.context import Ctx


def catalogue_hash(specs):
    raw = "|".join(sorted("%s:%s" % (s.id, s.catalogue) for s in specs)) + "|" + VERSION
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _run_one(spec, ctx):
    t0 = time.time()
    try:
        st, detail, found, extra = spec.fn(ctx)
    except Exception as exc:   # a check that breaks is a result, not a crash and never a green
        st, detail, found, extra = "error", "%s: %s" % (type(exc).__name__, exc), [], {}
        if os.environ.get("INFIRMARY_DEBUG"):
            raise
    took = int((time.time() - t0) * 1000)
    return {"check": spec.id, "catalogue": spec.catalogue, "title": spec.title, "state": st, "detail": detail, "info": list(extra.get("info") or []),
            "took_ms": took, "remote": spec.remote, "family": spec.family, "reason_class": unknowns.CHECK_BROKE if st == "error" else None, "extra": extra, "findings": found, "probed": extra.get("probed", []),
            "windows": extra.get("windows") or {}, "drift_checked": extra.get("drift_checked") or [], "local": extra.get("local") or {}}


def select(specs, cfg, only=None):
    off = set(cfg["doctor"]["disabled"])
    out = []
    for s in specs:
        if s.id in off:
            continue
        if only and not (s.id in only or str(s.catalogue) in only):
            continue
        out.append(s)
    return out


def _seen_path(sd):
    return os.path.join(sd, "seen.json")


def apply_durations(findings, cfg, sd, now, persist=True):
    """Each finding must have been seen for its `for_s` before it counts (the condition's `for` duration), and is
    forgotten once absent for `clear_s`. State lives in the tool's own directory."""
    d = cfg["doctor"]
    seen = {}
    try:
        with open(_seen_path(sd), encoding="utf-8") as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    if not isinstance(seen, dict):
        seen = {}
    live = {}
    for f in findings:
        old = seen.get(f["id"]) or {}
        first = old.get("first") if old and now - old.get("last", 0) <= d["clear_s"] else now
        live[f["id"]] = {"first": first, "last": now}
        f["first_seen"], f["age_s"] = first, int(now - first)
        need = d["for_s"].get(f["kind"], 0) if d["hysteresis"] else 0
        f["pending"] = f["age_s"] < need
        if f["pending"]:
            f["qualifiers"].append("needs %ds" % need)
    for fid, row in seen.items():
        if fid not in live and now - row.get("last", 0) <= d["clear_s"]:
            live[fid] = row
    if persist:
        try:
            os.makedirs(sd, exist_ok=True)
            tmp = _seen_path(sd) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(live, fh)
            os.replace(tmp, _seen_path(sd))
        except OSError:
            pass


def flags(results, findings, ctx, cfg):
    """Per-host advisory flags for the router and the pool. None means not checked (never false by silence)."""
    hosts = {ctx.host_id: {"drain": None, "hung": None, "disk_critical": None, "capability_drift": None}}
    for h in cfg["doctor"]["hosts"]:
        hosts.setdefault(h["id"], {"drain": None, "hung": None, "disk_critical": None, "capability_drift": None})
    by = dict((r["check"], r) for r in results)
    if by.get("disk-exhaustion", {}).get("state") in ("ok", "finding"):
        hosts[ctx.host_id]["disk_critical"] = False
    if by.get("memory-swap", {}).get("state") in ("ok", "finding"):
        hosts[ctx.host_id]["drain"] = False
    for r in results:
        if r["check"] == "hung-host" and r["state"] in ("ok", "finding"):
            for hid in r.get("probed", []):
                hosts.setdefault(hid, {"drain": None, "hung": None, "disk_critical": None, "capability_drift": None})["hung"] = False
    for r in results:
        if r["check"] == "windows-capabilities" and r["state"] in ("ok", "finding"):
            for hid in r.get("drift_checked", []):
                hosts.setdefault(hid, {"drain": None, "hung": None, "disk_critical": None, "capability_drift": None})["capability_drift"] = False
    for f in findings:
        if f["pending"]:
            continue
        row = hosts.setdefault(f["host"], {"drain": None, "hung": None, "disk_critical": None, "capability_drift": None})
        if f["kind"] == "disk-low" and f["status"] == "CRITICAL":
            row["disk_critical"] = True
            row["drain"] = True
        if f["kind"] == "memory-pressure" and f["status"] == "CRITICAL":
            row["drain"] = True
        if f["kind"] == "host-memory-short" and f["status"] == "CRITICAL":
            row["drain"] = True
        if f["kind"] in ("wsl-vm-near-cap", "wsl-vm-spinning", "tmpfs-full", "firmware-virtualization-off", "hypervisor-not-running", "hypervisor-launch-failed") and f["status"] == "CRITICAL":
            row["drain"] = True
        if f["kind"] == "capability-drift":
            row["capability_drift"] = True
        if f["kind"] in ("host-hung",):
            row["hung"] = True
            row["drain"] = True
        if f["kind"] in ("remote-draining", "remote-canary-failing"):
            row["drain"] = True
    return hosts


def attach_dry_runs(findings, cfg, ctx):
    """Opt-in: run each owning script's dry run once and keep the end of its output. Argv comes only from the check
    that raised the finding, so configuration cannot add a command. Output is a list the owner already prints."""
    if not cfg["doctor"]["dry_runs"]:
        return 0
    cache, n = {}, 0
    for f in findings:
        if f["pending"]:
            continue
        for h in finding.all_hows(f):
            if not h.get("dry_run"):
                continue
            key = (tuple(h["dry_run"]), h.get("cwd"))
            if key not in cache:
                res = execute.run(h["dry_run"], timeout=cfg["doctor"]["dry_run_timeout_s"], cwd=h.get("cwd"))
                body = (res.out or res.err).strip().splitlines()
                cache[key] = {"rc": res.rc, "missing": res.missing, "timed_out": res.timed_out, "took_ms": res.took_ms,
                              "lines": len(body), "tail": body[-12:]}
                n += 1
            h["dry_run_output"] = cache[key]
    return n


def load(cfg):
    """(adapter hooks, every check spec): loaded once per command and shared by the check, the repairs and re-detection."""
    try:
        hooks = registry.load_adapter(cfg) or {}
    except registry.AdapterError as exc:    # the named adapter's checks are missing: that is an error row, not a smaller, greener run
        why = str(exc)
        hooks = {"unloaded": exc.short}
        return hooks, registry.core_specs() + [registry.Spec("adapter", 0, "Adapter", lambda ctx: ("error", why, [], {}))]
    return hooks, registry.core_specs() + list(hooks.get("checks") or [])


def redetect(cfg, loaded, check_ids):
    """Run the named checks again, now, with a fresh context (a fresh process table): {finding id: finding}.

    This is the reading a repair takes immediately before it acts and again after, to verify. It writes nothing (no
    disk history, no for/clear memory) and ignores the for durations: the question is only "is it still there"."""
    hooks, specs = loaded
    quiet = dict(cfg, doctor=dict(cfg["doctor"], _persist=False))
    ctx = Ctx(quiet)
    ctx.adapter = hooks
    out = {}
    for s in specs:
        if s.id in check_ids and not s.remote:
            r = _run_one(s, ctx)
            for f in r["findings"]:
                out[f["id"]] = f
    return out, ctx


def run(cfg, only=None, persist=True, now=None, loaded=None):
    cfg["doctor"]["_persist"] = persist
    ctx = Ctx(cfg, now=now)
    sd = state.state_dir(cfg)
    hooks, every = loaded or load(cfg)
    ctx.adapter = hooks
    specs = select(every, cfg, only)
    t0 = time.time()
    results, threads = [], []
    lock = threading.Lock()

    def worker(spec):
        r = _run_one(spec, ctx)
        with lock:
            results.append(r)

    for s in specs:
        if s.remote:
            th = threading.Thread(target=worker, args=(s,))
            th.daemon = True
            th.start()
            threads.append((th, s))
    local_ms = 0
    for s in specs:
        if not s.remote:
            worker(s)
    local_ms = int((time.time() - t0) * 1000)
    deadline = t0 + cfg["doctor"]["remote_budget_ms"] / 1000.0
    for th, s in threads:
        th.join(max(0.0, deadline - time.time()))
        if th.is_alive():
            with lock:
                results.append({"check": s.id, "catalogue": s.catalogue, "title": s.title, "state": "error",
                                "detail": "did not finish within the remote budget (%d ms)" % cfg["doctor"]["remote_budget_ms"],
                                "took_ms": cfg["doctor"]["remote_budget_ms"], "remote": True, "findings": [], "reason_class": unknowns.OVERRAN})
    results.sort(key=lambda r: (r["catalogue"], r["check"]))
    incidents.cover_unreadable(results, ctx.host_id)
    findings = [f for r in results for f in r["findings"]]
    apply_durations(findings, cfg, sd, ctx.now, persist=persist)
    ran = attach_dry_runs(findings, cfg, ctx)
    findings.sort(key=lambda f: (-finding.RANK[f["status"]], f["catalogue"], f["id"]))
    t_inc = time.time()
    complete = {s.id for s in select(every, cfg) if s.family == incidents.FAMILY} <= {s.id for s in specs}
    inc = incidents.fold(findings, results, cfg, sd, ctx.now, ctx.host_id, persist=persist, complete=complete)
    incidents_ms = int((time.time() - t_inc) * 1000)
    if hooks.get("unloaded"):
        inc["not_read"] = hooks["unloaded"]
    live = [f for f in findings if not f["pending"]]
    worst = "OK"
    for f in live:
        if finding.RANK[f["status"]] > finding.RANK[worst]:
            worst = f["status"]
    if worst == "OK" and any(r["state"] in ("unknown", "error") for r in results):
        worst = "UNKNOWN"
    took = int((time.time() - t0) * 1000)
    b = ctx.beat()
    doc = {
        "schema": finding.DOCTOR_SCHEMA, "schema_version": finding.DOCTOR_SCHEMA_VERSION, "name": "infirmary", "tool_version": VERSION,
        "catalogue_hash": catalogue_hash(every),
        "checked_at": finding.now_iso(ctx.now), "at_epoch": int(ctx.now), "mode": cfg["mode"], "repair_mode": livemod.label(cfg),
        "live_repair": livemod.summary(cfg), "host": ctx.host,
        "status": worst, "exit_code": finding.EXIT[worst],
        "flags": flags(results, findings, ctx, cfg),
        "checks": [{k: v for k, v in r.items() if k not in ("findings", "probed", "windows", "drift_checked", "local", "extra")} for r in results],
        "windows": _windows_detail(results),
        "local": _local_detail(results),
        "findings": findings,
        "incidents": inc,
        "beat": {"level": b["level"], "age_s": b["age_s"]} if b else None,
        "dry_runs_run": ran,
        "cost": {"took_ms": took, "local_ms": local_ms, "budget_ms": cfg["doctor"]["tick_budget_ms"],
                 "over_budget": local_ms > cfg["doctor"]["tick_budget_ms"], "rss_mb": _rss_mb(), "incidents_ms": incidents_ms},
    }
    doc["unknowns"] = unknowns.of(doc)
    return doc


def _windows_detail(results):
    """What the Windows-side checks saw, per host (doctor.json `windows`): the WSL VM, the parentless-process scan, the capabilities and
    the platform (virtualization, hypervisor, restarts, the pending-restart fact in Harbor's shape, the update policy)."""
    out = {}
    for r in results:
        for hid, part in (r.get("windows") or {}).items():
            out.setdefault(hid, {}).update(part)
    return out


def _local_detail(results):
    """What the checks of this machine saw beyond their findings (doctor.json `local`, keyed by check id): the RAM-backed volumes and scratch
    groups, the slot records, the WSL interop entry. A check that could not read its part leaves it out."""
    out = {}
    for r in results:
        out.update(r.get("local") or {})
    return out


def _rss_mb():
    try:
        import resource
        import sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss / (1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0), 1)
    except (ImportError, ValueError):
        return None


def write_doctor_json(doc, cfg):
    path = cfg["doctor"]["out"] or os.path.join(state.state_dir(cfg), "doctor.json")
    path = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
        return path
    except OSError:
        return None
