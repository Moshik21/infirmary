"""Catalogue 5: a runner clobbers a checkout. A job that ran on another OS and copied its `node_modules` back leaves
native-binding packages built for the wrong platform (`*-win32-*` on Linux or macOS, `*-linux-*` on Windows) and none
for this one, so the next run dies with "Cannot find native binding". Detection lists package directory names only;
no file is opened and no worktree is touched."""
import os
import re

from infirmary.doctor import finding, registry

PLAT = re.compile(r"(?:^|[-.])(win32|darwin|linux|freebsd|android|openbsd|sunos|aix)(?=[-.]|$)")
HOST = {"linux": "linux", "wsl": "linux", "macos": "darwin", "windows": "win32"}


def _family(name):
    """('@scope/base-*', 'win32') for a platform-specific package name, else None."""
    m = PLAT.search(name)
    if not m:
        return None
    return name[:m.start()] + ("-" if name[m.start():m.start() + 1] in "-." else "") + "*", m.group(1)


def _names(nm):
    try:
        top = sorted(os.listdir(nm))
    except OSError:
        return None
    out = []
    for n in top:
        if n.startswith("@"):
            try:
                out += [n + "/" + c for c in sorted(os.listdir(os.path.join(nm, n)))]
            except OSError:
                continue
        elif n == ".pnpm":
            try:
                for c in sorted(os.listdir(os.path.join(nm, n))):
                    base = re.sub(r"@[^@/]+$", "", c).replace("+", "/", 1)
                    out.append(base)
            except OSError:
                continue
        elif not n.startswith("."):
            out.append(n)
    return out


def scan(root, host_plat):
    nm = os.path.join(root, "node_modules")
    names = _names(nm)
    if names is None:
        return None, []
    fams = {}
    for n in names:
        fp = _family(n)
        if fp:
            fams.setdefault(fp[0], set()).add(fp[1])
    bad = sorted((f, sorted(p)) for f, p in fams.items() if host_plat not in p)
    return len(names), bad


def check(ctx):
    roots = []
    hook = ctx.adapter.get("clobber_roots")
    if hook:
        roots += hook(ctx)
    roots += [dict(r) if isinstance(r, dict) else {"path": r, "label": r} for r in ctx.d["clobber"]["roots"]]
    if not roots:
        return registry.skip("no worktrees to scan (the adapter names none and doctor.clobber.roots is empty)")
    plat = HOST.get(ctx.osk, "linux")
    findings, scanned, with_nm = [], 0, 0
    for r in roots:
        path = ctx.expand(r["path"])
        n, bad = scan(path, plat)
        scanned += 1
        if n is None:
            continue
        with_nm += 1
        if not bad:
            continue
        fams = ["%s (only %s)" % (f, "/".join(p)) for f, p in bad[:5]]
        findings.append(finding.make(
            "clobbered-checkout", 5, "clobbered-bindings", path, "WARNING", ctx.host_id, r.get("label") or path,
            "dependencies hold another platform's native bindings",
            "node_modules has %d native-binding package(s) built for another OS and none for %s: %s" % (len(bad), plat, "; ".join(fams)),
            evidence=[finding.fact("node_modules listing", "%d family(ies) without a %s build: %s" % (len(bad), plat, "; ".join(fams)), os.path.join(path, "node_modules"))],
            how=finding.how("the lane's worker", "Reinstalling dependencies from the lockfile in that worktree repairs it; the Infirmary never edits a worktree, so this goes to the lane's worker as a plan."),
            recommendation="Ask that lane's worker to remove node_modules and reinstall from the lockfile in its own worktree before the next run.",
            lk_kind="service"))
    if not with_nm:
        return registry.ok("%d worktree(s) listed, none has a node_modules" % scanned)
    return registry.found(findings, "%d worktree(s) with dependencies checked, %d clobbered" % (with_nm, len(findings)))


SPECS = [registry.Spec("clobbered-checkout", 5, "Foreign native bindings in checkouts", check)]
