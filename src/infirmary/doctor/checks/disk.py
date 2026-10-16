"""Catalogue 9: disk exhaustion. Free space per volume, its growth, and (only when a volume is sick) a size
inventory of the known agent scratch places so the card can say where the space went. Sizes only: the
Infirmary never deletes a file and never runs an owner's apply flag (design 5.2, the 2026-09-26 find incident)."""
import json
import os
import shutil
import time

from infirmary.doctor import finding, registry

GB = float(1 << 30)


def volumes(ctx):
    d = ctx.d["disk"]
    items = list(d["paths"])
    if not items:
        items = [{"path": "~", "label": "home"}]
        if ctx.osk == "wsl" and os.path.isdir("/mnt/c"):
            items.append({"path": "/mnt/c", "label": "windows-host-drive"})
        if ctx.osk == "macos" and os.path.isdir("/System/Volumes/Data"):
            items = [{"path": "/System/Volumes/Data", "label": "data-volume"}]
    out = []
    for it in items:
        it = {"path": it} if isinstance(it, str) else dict(it)
        it["path"] = ctx.expand(it["path"])
        it.setdefault("label", it["path"])
        for k in ("warn_gb", "crit_gb", "warn_pct", "crit_pct"):
            it.setdefault(k, d[k])
        out.append(it)
    return out


def walk_size(root, deadline):
    """Allocated bytes under root, not following symlinks nor leaving the device; (bytes, files, complete)."""
    total, files, complete = 0, 0, True
    try:
        dev = os.lstat(root).st_dev
    except OSError:
        return 0, 0, False
    stack = [root]
    while stack:
        if time.time() > deadline:
            return total, files, False
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            complete = False
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    complete = False
                    continue
                if e.is_symlink() or st.st_dev != dev:
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    total += getattr(st, "st_blocks", 0) * 512 or st.st_size
                    files += 1
    return total, files, complete


def _history_path(ctx):
    from infirmary.core import state
    return os.path.join(state.state_dir(ctx.cfg), "disk-history.json")


def _growth(ctx, label, used):
    """Bytes per day, from the sample nearest 24 h old (at least 1 h), and the new history to keep."""
    path = _history_path(ctx)
    hist = {}
    try:
        with open(path, encoding="utf-8") as fh:
            hist = json.load(fh)
    except (OSError, ValueError):
        hist = {}
    rows = [r for r in hist.get(label, []) if isinstance(r, list) and len(r) == 2]
    rate = None
    old = [r for r in rows if ctx.now - r[0] >= 3600]
    if old:
        base = min(old, key=lambda r: abs((ctx.now - r[0]) - 86400))
        span = ctx.now - base[0]
        if span >= 3600:
            rate = (used - base[1]) / span * 86400
    if not rows or ctx.now - rows[-1][0] >= 300:
        rows.append([int(ctx.now), used])
    hist[label] = rows[-ctx.d["disk"]["history_samples"]:]
    return rate, hist


def check(ctx):
    vols = volumes(ctx)
    findings, seen, newhist = [], [], {}
    d = ctx.d["disk"]
    for v in vols:
        try:
            total, _, free = shutil.disk_usage(v["path"])
            st = None
        except OSError as exc:
            seen.append("%s unreadable (%s)" % (v["label"], exc.strerror or exc))
            continue
        if total <= 0:
            continue
        used = total - free
        free_gb, free_pct = free / GB, 100.0 * free / total
        rate, hist = _growth(ctx, v["label"], used)
        newhist.update(hist)
        seen.append("%s %.1f GB free of %.0f GB (%.0f%%)" % (v["label"], free_gb, total / GB, free_pct))
        status = None
        if free_gb <= v["crit_gb"] or free_pct <= v["crit_pct"]:
            status = "CRITICAL"
        elif free_gb <= v["warn_gb"] or free_pct <= v["warn_pct"]:
            status = "WARNING"
        ev = [finding.fact("disk usage", "%.1f GB free of %.0f GB total (%.1f%%); thresholds warn %g GB / %g%%, critical %g GB / %g%%" % (
            free_gb, total / GB, free_pct, v["warn_gb"], v["warn_pct"], v["crit_gb"], v["crit_pct"]), v["path"])]
        if rate is not None:
            ev.append(finding.fact("disk history", "used space %s %.1f GB/day over the last day" % ("grew" if rate >= 0 else "shrank", abs(rate) / GB)))
        if status:
            hows = []
            inv = []
            inv = _inventory(ctx, v, st)
            if inv:
                ev.append(finding.fact("inventory", "; ".join(inv)))
            hows = list((ctx.adapter.get("dry_runs") or {}).get("disk-exhaustion", []))
            findings.append(finding.make(
                "disk-exhaustion", 9, "disk-low", v["label"], status, ctx.host_id, "%s:%s" % (ctx.host_id, v["label"]),
                "disk %s is %s full" % (v["label"], "%.0f%%" % (100 - free_pct)),
                "%.1f GB free of %.0f GB on %s%s" % (free_gb, total / GB, v["label"], ("; used space grows %.1f GB/day" % (rate / GB)) if rate and rate > 0 else ""),
                evidence=ev, how=hows[0] if hows else None, hows=hows[1:],
                recommendation="Free space on this volume before new lanes start; the Infirmary lists where it went and never deletes a file itself. "
                               "Review the owner's dry-run list first; applying it is a person's call.",
                lk_kind="disk", retires_with=[], target={"path": v["path"], "label": v["label"], "free_gb": round(free_gb, 2)}))
        elif rate and rate > 0 and free / rate < d["growth_days"]:
            findings.append(finding.make(
                "disk-exhaustion", 9, "disk-growth", v["label"], "WARNING", ctx.host_id, "%s:%s" % (ctx.host_id, v["label"]),
                "disk %s fills in about %.1f days" % (v["label"], free / rate),
                "%.1f GB free and used space grows %.1f GB/day" % (free_gb, rate / GB), evidence=ev,
                recommendation="Find what grows before the volume reaches its threshold.", lk_kind="disk"))
    if ctx.cfg["doctor"].get("_persist", True):
        _write_history(ctx, newhist)
    if not seen:
        return registry.unknown("no volume could be read")
    return registry.found(findings, "; ".join(seen))


def _write_history(ctx, hist):
    if not hist:
        return
    path = _history_path(ctx)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(hist, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def _inventory(ctx, vol, st):
    paths = []
    hook = ctx.adapter.get("disk_inventory")
    if hook:
        paths += hook(ctx)
    paths += [dict(p) if isinstance(p, dict) else {"path": p, "label": p} for p in ctx.d["disk"].get("inventory", [])]
    try:
        dev = os.stat(vol["path"]).st_dev
    except OSError:
        return []
    keep = []
    for p in paths:
        p = dict(p, path=ctx.expand(p["path"]))
        try:
            if os.path.isdir(p["path"]) and os.stat(p["path"]).st_dev == dev:
                keep.append(p)
        except OSError:
            continue
    out, per = [], ctx.d["disk"]["inventory_s"] / float(max(1, len(keep)))
    for p in keep:
        size, files, done = walk_size(p["path"], time.time() + per)
        out.append("%s %s%.1f GB (%d files)" % (p["label"], "" if done else "at least ", size / GB, files))
    return out


SPECS = [registry.Spec("disk-exhaustion", 9, "Disk space", check)]
