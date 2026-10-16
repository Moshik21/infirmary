"""Catalogue 20: scratch a job left behind, and a RAM-backed volume that filled up.

A `/tmp` that is a tmpfs lives in memory and swap, so headless-browser profiles (about 200 MB each) and test-floor data directories
that nobody removed do two harms at once: tools fail with ENOSPC, and the memory is gone. Two findings:

  scratch-leaked  the entries of a configured group (a glob, e.g. `/tmp/browser-profile-*`) that nothing uses any more: untouched for
                  `min_age_s`, named by no live process (command line, working directory or open file) and not owned by a live pid
                  that the name carries. Sizes, count, oldest age and what was checked are the evidence; a group counts once its
                  leaked entries add up to `min_mb`.
  tmpfs-full      a tmpfs or ramfs mount at or above `tmpfs_warn_pct` used (`tmpfs_crit_pct`: critical, and the host is flagged to drain).

Detection only. The Infirmary never deletes: the owner's own reaper, run by a person, is the "how". An entry that anything still
references is never listed as leaked, and an OS that cannot say what a process has open judges by command lines alone and says so.
"""
import glob
import os
import re
import time

from infirmary.core import sysinfo
from infirmary.doctor import finding, registry
from infirmary.doctor.checks import disk

MB = float(1 << 20)
GB = float(1 << 30)
SKIP_MOUNTS = ("/sys", "/proc", "/dev/pts", "/dev/mqueue", "/run/lock", "/run/credentials")


def _size(n):
    return "%.1f GB" % (n / GB) if n >= GB else "%.0f MB" % (n / MB)


def _ago(secs):
    secs = int(max(0, secs))
    if secs >= 86400:
        return "%dd%02dh" % (secs // 86400, secs % 86400 // 3600)
    if secs >= 3600:
        return "%dh%02dm" % (secs // 3600, secs % 3600 // 60)
    return "%dm" % (secs // 60) if secs >= 60 else "%ds" % secs


def groups(ctx):
    """Configured groups (`doctor.scratch.groups`) and the adapter's. A group: {label, glob (a pattern or a list), min_age_s, min_mb, owner_pid_re}."""
    out = []
    for g in list(ctx.d["scratch"]["groups"]) + list((ctx.adapter.get("scratch_groups") or (lambda c: []))(ctx)):
        if not isinstance(g, dict) or not g.get("glob"):
            continue
        pats = g["glob"] if isinstance(g["glob"], list) else [g["glob"]]
        out.append({"label": str(g.get("label") or pats[0]), "globs": [ctx.expand(p) for p in pats], "min_age_s": g.get("min_age_s"),
                    "min_mb": g.get("min_mb"), "owner_pid_re": g.get("owner_pid_re")})
    return out


def tmpfs_mounts(ctx):
    """[{path, total, used, pct}] for each RAM-backed mount worth watching, or None where there is no mount table."""
    try:
        with open(os.path.join(ctx.proc_root, "mounts"), encoding="utf-8", errors="replace") as fh:
            rows = fh.read().splitlines()
    except OSError:
        return None
    out, seen, shapes = [], set(), set()
    for row in rows:
        parts = row.split()
        if len(parts) < 3 or parts[2] not in ("tmpfs", "ramfs"):
            continue
        path = parts[1].replace("\\040", " ")
        if path in seen or any(path == s or path.startswith(s + "/") for s in SKIP_MOUNTS):
            continue
        seen.add(path)
        try:
            st = os.statvfs(path)
        except OSError:
            continue
        total, used, avail = st.f_blocks * st.f_frsize, (st.f_blocks - st.f_bfree) * st.f_frsize, st.f_bavail * st.f_frsize
        if total < ctx.d["scratch"]["tmpfs_min_mb"] * MB or used + avail <= 0:
            continue
        if (total, used, avail) in shapes:        # a bind mount of a volume already listed (WSL mounts one under several names)
            continue
        shapes.add((total, used, avail))
        out.append({"path": path, "total": total, "used": used, "avail": avail, "pct": 100.0 * used / (used + avail)})
    return out


def _references(ctx):
    """What this user's processes name: (text of every command line, [(pid, comm, path)] for working directories and open files, counts, deep).
    `deep` is False where an OS gives no /proc to read directories and descriptors from (command lines alone are judged then)."""
    procs = ctx.processes()
    text = "\0".join(p["cmdline"] for p in procs)
    paths, n_cwd, n_fd = [], 0, 0
    deep = ctx.osk != "macos" and os.path.isdir(ctx.proc_root)
    if deep:
        end = time.time() + 1.0
        for p in ctx.my_processes():
            if time.time() > end:
                deep = False
                break
            comm = os.path.basename(p["argv"][0]) if p["argv"] else "?"
            c = sysinfo.cwd(ctx.proc_root, p["pid"])
            if c:
                paths.append((p["pid"], comm, c))
                n_cwd += 1
            for t in sysinfo.open_paths(ctx.proc_root, p["pid"]) or []:
                if t.startswith("/") or ":" in t[:3]:
                    paths.append((p["pid"], comm, t))
                    n_fd += 1
    return text, paths, {"cmdlines": len(procs), "cwds": n_cwd, "fds": n_fd}, deep


def _used_by(path, text, paths, procs):
    """The (pid, command) of a live process that names `path` (command line, working directory or open file), else None."""
    if path in text:
        rx = re.compile(re.escape(path) + r"(?![\w.-])")
        for p in procs:
            if path in p["cmdline"] and rx.search(p["cmdline"]):
                return p["pid"], os.path.basename(p["argv"][0]) if p["argv"] else "?"
    for pid, comm, t in paths:
        if t == path or t.startswith(path + "/"):
            return pid, comm
    return None


def evaluate(ctx, g, refs, deadline):
    """-> the facts about one group: entries, leaked, in use, young, with sizes of the leaked within the deadline."""
    cfg = ctx.d["scratch"]
    text, paths, counts, deep = refs
    procs = ctx.processes()
    by_pid = dict((p["pid"], p) for p in procs)
    min_age = g["min_age_s"] if g["min_age_s"] is not None else cfg["min_age_s"]
    rx = None
    if g["owner_pid_re"]:
        try:
            rx = re.compile(g["owner_pid_re"])
        except re.error:
            rx = None
    names, seen = [], set()
    for pat in g["globs"]:
        for path in sorted(glob.glob(pat)):
            if path not in seen:
                seen.add(path)
                names.append(path)
    leaked, in_use, young, ignored = [], [], [], 0
    for path in names:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if os.path.islink(path) or not (os.path.isdir(path) or os.path.isfile(path)):
            ignored += 1
            continue
        age = ctx.now - st.st_mtime
        owner = None
        if rx:
            m = rx.search(os.path.basename(path))
            if m and m.groups() and m.group(1).isdigit() and int(m.group(1)) in by_pid:
                owner = (int(m.group(1)), os.path.basename(by_pid[int(m.group(1))]["argv"][0]) if by_pid[int(m.group(1))]["argv"] else "?")
        used = owner or _used_by(path, text, paths, procs)
        row = {"path": path, "name": os.path.basename(path), "age": age, "size": None, "dir": os.path.isdir(path), "used_by": used}
        (in_use if used else young if age < min_age else leaked).append(row)
    left = len(leaked)
    complete = True
    for row in sorted(leaked, key=lambda r: r["age"], reverse=True):
        now = time.time()
        if now >= deadline:
            complete = False
            break
        share = min(deadline, now + (deadline - now) / max(1, left))
        left -= 1
        if row["dir"]:
            size, _, done = disk.walk_size(row["path"], share)
        else:
            size, done = os.lstat(row["path"]).st_blocks * 512, True
        row["size"] = size
        complete = complete and done
    return {"group": g, "entries": len(names) - ignored, "leaked": leaked, "in_use": in_use, "young": young, "sized_all": complete and all(r["size"] is not None for r in leaked),
            "counts": counts, "deep": deep, "min_age": min_age}


def _group_finding(ctx, res):
    g, cfg = res["group"], ctx.d["scratch"]
    leaked = res["leaked"]
    got = sum(r["size"] or 0 for r in leaked)
    min_mb = g["min_mb"] if g["min_mb"] is not None else cfg["min_mb"]
    if not leaked or got < min_mb * MB:
        return None
    ages = [r["age"] for r in leaked]
    first = os.path.dirname(leaked[0]["path"])
    try:
        st = os.statvfs(first)
        used, avail = (st.f_blocks - st.f_bfree) * st.f_frsize, st.f_bavail * st.f_frsize
        pct = 100.0 * used / (used + avail) if used + avail else None
        total = st.f_blocks * st.f_frsize
    except OSError:
        pct, total = None, None
    crit = pct is not None and pct >= cfg["tmpfs_crit_pct"]
    lower = "" if res["sized_all"] else "at least "
    ev = [finding.fact("scratch inventory", "%d of %d entries matching %s are leaked: %s%s in all, the oldest untouched for %s, the newest for %s; %d in use or owned by a live process, %d younger than %s" % (
        len(leaked), res["entries"], " or ".join(g["globs"]), lower, _size(got), _ago(max(ages)), _ago(min(ages)), len(res["in_use"]), len(res["young"]), _ago(res["min_age"])), first)]
    big = sorted(leaked, key=lambda r: -(r["size"] or 0))[:4]
    ev.append(finding.fact("scratch inventory", "largest: " + "; ".join("%s (%s, untouched %s)" % (r["name"], _size(r["size"] or 0) if r["size"] is not None else "size not read", _ago(r["age"])) for r in big)))
    c = res["counts"]
    ev.append(finding.fact("process table", "no live process names any of them: checked %d command line(s)%s" % (
        c["cmdlines"], ", %d working director(ies) and %d open file(s) of this user's processes" % (c["cwds"], c["fds"]) if res["deep"] else " only (this OS gives no view of what a process has open)")))
    if pct is not None:
        ev.append(finding.fact("volume", "%s is %.0f%% used of %s%s" % (first, pct, _size(total), " (%s; leaked entries are %.0f%% of it)" % ("RAM-backed" if _is_tmpfs(ctx, first) else "disk", 100.0 * got / total) if total else ""), first))
    for r in res["in_use"][:3]:
        ev.append(finding.fact("process table", "left alone, in use: %s (pid %d, %s)" % (r["name"], r["used_by"][0], r["used_by"][1])))
    hows = list((ctx.adapter.get("dry_runs") or {}).get("scratch-leaks", []))
    f = finding.make(
        "scratch-leaks", 20, "scratch-leaked", g["label"], "CRITICAL" if crit else "WARNING", ctx.host_id, "%s:%s" % (ctx.host_id, g["label"]),
        "%d leaked %s hold %s%s" % (len(leaked), g["label"], lower, _size(got)),
        "%d entries of %s were left behind (nothing uses them, untouched for %s to %s) and hold %s%s%s" % (
            len(leaked), g["label"], _ago(min(ages)), _ago(max(ages)), lower, _size(got), "; the volume is %.0f%% full" % pct if pct is not None and pct >= cfg["tmpfs_warn_pct"] else ""),
        evidence=ev, how=hows[0] if hows else finding.how("the job that created them", "Whoever owns the job removes its scratch; the Infirmary lists these and deletes nothing."),
        hows=hows[1:], recommendation="Check that no job still needs these directories, then remove them with the owner's reaper (its dry-run list first). A full RAM-backed volume also costs memory.",
        lk_kind="disk" if not _is_tmpfs(ctx, first) else "capacity", retires_with=[], qualifiers=[] if res["deep"] else ["judged by command lines only"],
        target={"group": g["label"], "paths": [r["path"] for r in leaked[:50]], "leaked": len(leaked), "bytes": got})
    return f


def _is_tmpfs(ctx, path):
    for m in ctx.once("scratch-mounts", lambda: tmpfs_mounts(ctx)) or []:
        if path == m["path"] or path.startswith(m["path"].rstrip("/") + "/"):
            return True
    return False


def _top_entries(path, deadline):
    out = []
    try:
        names = os.listdir(path)
    except OSError:
        return out, False
    complete = True
    for n in names:
        now = time.time()
        if now >= deadline:
            complete = False
            break
        p = os.path.join(path, n)
        try:
            if os.path.islink(p):
                continue
            if os.path.isdir(p):
                size, _, done = disk.walk_size(p, min(deadline, now + 0.25))
                complete = complete and done
            else:
                size = os.lstat(p).st_blocks * 512
        except OSError:
            continue
        out.append((size, n))
    return sorted(out, reverse=True), complete


def _tmpfs_finding(ctx, m, leaked_here, deadline):
    cfg = ctx.d["scratch"]
    crit = m["pct"] >= cfg["tmpfs_crit_pct"]
    top, complete = _top_entries(m["path"], deadline)
    ev = [finding.fact("mount table", "%s is a RAM-backed volume of %s, %s used (%.0f%%), %s free; what it holds is memory and swap, not disk" % (
        m["path"], _size(m["total"]), _size(m["used"]), m["pct"], _size(m["avail"])), m["path"])]
    if top:
        ev.append(finding.fact("inventory", "largest entries%s: %s" % ("" if complete else " (some not sized in time)", "; ".join("%s %s" % (n, _size(s)) for s, n in top[:5]))))
    for label, count, size in leaked_here:
        ev.append(finding.fact("scratch inventory", "%d leaked %s here hold %s (see scratch-leaked)" % (count, label, _size(size))))
    return finding.make(
        "scratch-leaks", 20, "tmpfs-full", m["path"], "CRITICAL" if crit else "WARNING", ctx.host_id, "%s:%s" % (ctx.host_id, m["path"]),
        "%s is %.0f%% full and it is RAM" % (m["path"], m["pct"]),
        "%s of %s used on the %s tmpfs: writes start failing with ENOSPC near 100%%, and what is stored there is not available to jobs as memory" % (_size(m["used"]), _size(m["total"]), m["path"]),
        evidence=ev, how=finding.how("the jobs that write there", "Free it through the owners' own cleanup; the Infirmary deletes nothing. Leaked entries, if any, are listed as scratch-leaked."),
        recommendation="Remove what nothing uses (the leaked entries first) before new jobs start; a tmpfs near 100% fails tools with ENOSPC and takes memory with it.",
        lk_kind="capacity", retires_with=[], target={"path": m["path"], "used_pct": round(m["pct"], 1)})


def check(ctx):
    cfg = ctx.d["scratch"]
    gs = groups(ctx)
    mounts = ctx.once("scratch-mounts", lambda: tmpfs_mounts(ctx))
    if not gs and mounts is None:
        return registry.skip("no scratch group configured (doctor.scratch.groups) and no mount table to read")
    findings, notes, local = [], [], {"groups": {}, "tmpfs": {}}
    deadline = time.time() + cfg["inventory_s"]
    leaked_by_mount, quiet = {}, 0
    if gs:
        if not ctx.have_processes():
            if mounts is None:
                return registry.unknown("no process table on this OS, so whether scratch is still in use cannot be judged")
            notes.append("scratch groups not judged: no process table")
        else:
            refs = _references(ctx)
            for g in gs:
                res = evaluate(ctx, g, refs, deadline)
                got = sum(r["size"] or 0 for r in res["leaked"])
                local["groups"][g["label"]] = {"entries": res["entries"], "leaked": len(res["leaked"]), "leaked_bytes": got,
                                               "in_use": len(res["in_use"]), "young": len(res["young"]), "sized_all": res["sized_all"],
                                               "oldest_leaked_age_s": int(max([r["age"] for r in res["leaked"]] or [0]))}
                f = _group_finding(ctx, res)
                if f:
                    findings.append(f)
                    first = os.path.dirname(res["leaked"][0]["path"])
                    leaked_by_mount.setdefault(first, []).append((g["label"], len(res["leaked"]), got))
                notes.append("%s: %d entr%s, %d leaked, %d in use" % (g["label"], res["entries"], "y" if res["entries"] == 1 else "ies", len(res["leaked"]), len(res["in_use"])))
    for m in mounts or []:
        local["tmpfs"][m["path"]] = {"total_mb": int(m["total"] / MB), "used_pct": round(m["pct"], 1)}
        if m["pct"] >= cfg["tmpfs_warn_pct"]:
            here = [x for first, lst in leaked_by_mount.items() if first == m["path"] or first.startswith(m["path"].rstrip("/") + "/") for x in lst]
            findings.append(_tmpfs_finding(ctx, m, here, max(deadline, time.time() + 1.0)))
        if m["pct"] >= 50 or m["pct"] >= cfg["tmpfs_warn_pct"]:
            notes.append("%s %.0f%% used of %s" % (m["path"], m["pct"], _size(m["total"])))
        else:
            quiet += 1
    if quiet:
        notes.append("%d other RAM-backed volume(s) under 50%%" % quiet)
    if not notes:
        return registry.ok("no RAM-backed volume of %d MB or more and no scratch group to look at" % cfg["tmpfs_min_mb"], local={"scratch-leaks": local})
    return registry.found(findings, "; ".join(notes), local={"scratch-leaks": local})


SPECS = [registry.Spec("scratch-leaks", 20, "Leaked scratch and full tmpfs", check)]
