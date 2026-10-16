"""Catalogue 2 (a hung WSL seen from the Windows side), 14 (what a Windows host offers a job) and 16 (farm processes a cut-off job left).

These read the same one-shot PowerShell probe as `hung-host` (`windows/probe.ps1`, sent over the host's ssh alias; the
answer is shared, so a host is asked once per tick). Everything is read-only: a process table, a few registry values, file
versions; no program on the host is run to learn a version and `wsl.exe` is never called (a hung WSL hangs it too). A host
that did not answer is `unknown` here and a finding of `hung-host`; a probe from an older Infirmary (no `v`) is `unknown`
for what it does not carry, never fine.
"""
import re

from infirmary.doctor import finding, registry
from infirmary.doctor.checks import hosts as hostmod

UNITS = {"": 1.0, "b": 1.0 / (1024 * 1024), "kb": 1.0 / 1024, "k": 1.0 / 1024, "mb": 1.0, "m": 1.0, "gb": 1024.0, "g": 1024.0, "tb": 1024.0 * 1024, "t": 1024.0 * 1024}
GPU_PREF = {"0": "system default", "1": "power saving", "2": "high performance"}
GPU_WORD = {"system": "system default", "power": "power saving", "high": "high performance"}


def windows_hosts(ctx):
    return [h for h in ctx.d["hosts"] if h.get("os") == "windows"]


def _list(x):
    return x if isinstance(x, list) else ([] if x in (None, "") else [x])


def wsl_cap_mb(row):
    """(MB, source): the memory WSL may use, from `.wslconfig` `memory=`, else half of the host's RAM (WSL's documented default)."""
    for ln in _list(row.get("wslconfig")):
        m = re.match(r"^\s*memory\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)\s*$", str(ln))
        if m and m.group(2).lower() in UNITS and m.group(2):
            return int(float(m.group(1)) * UNITS[m.group(2).lower()]), ".wslconfig memory=%s%s" % (m.group(1), m.group(2))
    total = row.get("total_mb")
    return (int(total / 2) if isinstance(total, (int, float)) and total > 0 else None), "the default (half of the host's memory)"


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _readable(ctx, hosts):
    """-> (rows, notes): the hosts whose probe answered, and why the others are not read."""
    rows, notes = [], []
    for h in hosts:
        row, why = hostmod.windows_row(ctx, h)
        if row is None:
            notes.append("%s: %s" % (h["id"], why))
        else:
            rows.append((h, row))
    return rows, notes


# ---- 2. the WSL VM seen from the Windows side ----------------------------------------------------------------------------
def check_wsl(ctx):
    hosts = windows_hosts(ctx)
    if not hosts:
        return registry.skip("no Windows host configured (doctor.hosts with os windows)")
    w, lim = ctx.d["windows"], ctx.d["windows"]["wsl"]
    rows, notes = _readable(ctx, hosts)
    if not rows:
        return registry.unknown("; ".join(notes) + " (WSL cannot be seen from the Windows side)", probed=[])
    findings, parts, doc = [], [], {}
    for h, row in rows:
        hid = h["id"]
        vm = bool(row.get("vmmem_n"))
        cap, cap_src = wsl_cap_mb(row)
        mb, cpu, avail, commit = _num(row.get("vmmem_mb")), _num(row.get("vmmem_cpu_pct")), _num(row.get("avail_mb")), _num(row.get("commit_pct"))
        cpu = min(cpu, 100.0) if cpu is not None else None       # a share of all CPUs; a sample cannot honestly say more
        pct = round(100.0 * mb / cap, 1) if vm and mb is not None and cap else None
        svc = row.get("wsl_service") or None
        doc[hid] = {"wsl": {"vm_running": vm, "vmmem_mb": mb if vm else None, "cap_mb": cap, "cap_source": cap_src, "cap_pct": pct, "vm_cpu_pct": cpu,
                            "service": svc, "probe": row.get("v", 1)}}
        ev = [finding.fact("windows probe", "the WSL VM (vmmem) %s%s; WSL service %s; caps: %s" % (
            "is running and holds %d MB" % mb if vm and mb is not None else "is not running", " (%.0f%% of its %d MB cap, %s)" % (pct, cap, cap_src) if pct is not None else "",
            svc or "not reported", "; ".join(str(x) for x in _list(row.get("wslconfig"))) or "none set in .wslconfig"), hid),
              finding.fact("windows probe", "the host has %s MB of %s MB free, commit %s%%%s" % (
                  avail, row.get("total_mb"), commit, ", the VM used %.0f%% of %d CPU(s) over a sample of under a second" % (cpu, row.get("cpus") or 0) if cpu is not None else "; no CPU sample (older probe or no VM)"), hid)]
        short = (avail is not None and avail < w["avail_warn_mb"]) or (commit is not None and commit >= w["commit_warn_pct"])
        # the guest, asked over its own alias: when it is silent the Windows side says whether its VM is there and busy
        answered, gerr = hostmod.guest_answers(ctx, h)
        g = h.get("guest") or {}
        gid = "%s/%s" % (hid, g.get("id", "guest"))
        if answered is False:
            if vm:
                findings.append(finding.make(
                    "wsl-vm", 2, "host-hung", gid, "CRITICAL", hid, gid, "the guest on %s does not answer while the host does" % hid,
                    "%s answers ssh but its guest %s does not (%s), and the Windows side sees the WSL VM running%s: the VM is up and not serving, which is what a hung WSL looks like" % (
                        hid, g["ssh"], gerr, " and spinning at %.0f%% of the host's CPUs" % cpu if cpu is not None and cpu >= lim["cpu_warn_pct"] else ""),
                    evidence=[finding.fact("ssh", "host answered, guest %s did not" % g["ssh"], g["ssh"])] + ev,
                    how=finding.how("a person at the machine", "Recovery stops every distribution and lane on that machine; it is a person's call after every lane there is recorded. "
                                    "The Infirmary never calls wsl.exe (a hung WSL hangs it too) and sets only the advisory hung and drain flags."),
                    recommendation="Treat the guest as hung; send no new work, record its lanes, then restart the VM or the host.", lk_kind="machine"))
            else:
                novirt = bool(re.search(r"HCS_E_HYPERV_NOT_INSTALLED|0x80370102", gerr or "", re.I))   # WSL cannot start: no hypervisor (catalogue 23, 24)
                findings.append(finding.make(
                    "wsl-vm", 2, "wsl-not-running", gid, "WARNING", hid, gid, "the WSL VM on %s is not running" % hid,
                    ("the guest %s does not answer and the Windows side sees no WSL VM (no vmmem process): WSL was stopped (a shutdown, a crash or the idle timeout), not hung" % g["ssh"])
                    if not novirt else ("the guest %s does not answer, the Windows side sees no WSL VM, and WSL says HCS_E_HYPERV_NOT_INSTALLED: it cannot start because no hypervisor "
                                        "runs (virtualization off in firmware is the usual cause; see the firmware-virtualization and hypervisor-launch checks)" % g["ssh"]),
                    evidence=[finding.fact("ssh", "host answered, guest %s did not" % g["ssh"], g["ssh"])] + ev,
                    how=finding.how("a person at the machine", "Starting WSL again is a person's call: every lane that lived in it is gone and needs relaunching."),
                    recommendation="Find out why WSL stopped, then restart it and relaunch what lived there.", lk_kind="machine"))
        elif vm:
            if cpu is not None and cpu >= lim["cpu_warn_pct"] and short:   # a busy VM on a host with room is just work; busy and starved is where WSL stalls
                crit = avail is not None and avail < w["avail_crit_mb"]
                findings.append(finding.make(
                    "wsl-vm", 2, "wsl-vm-spinning", hid, "CRITICAL" if crit else "WARNING", hid, hid, "the WSL VM on %s is using nearly all of the host's CPUs on a host short of memory" % hid,
                    "the VM used %.0f%% of the host's %d CPU(s) over a short sample while the host is short of memory (%s MB free, commit %s%%)%s" % (
                        cpu, row.get("cpus") or 0, avail, commit, "; the guest %s still answers" % g["ssh"] if answered else "; the guest %s waits for an additional ssh check, so whether it answers is not known" % g["ssh"] if g else "; no guest alias is configured, so whether it still answers is not known"),
                    evidence=ev, how=finding.how("a person at the machine", "Spinning is a sign, not a proof of a hang: the guest's own load says what runs. The Infirmary stops nothing."),
                    recommendation="Look at the guest's load and its memory stall; send no new work until it settles.", lk_kind="machine"))
            if pct is not None and pct >= lim["cap_warn_pct"]:
                crit = pct >= lim["cap_crit_pct"] and short
                findings.append(finding.make(
                    "wsl-vm", 2, "wsl-vm-near-cap", hid, "CRITICAL" if crit else "WARNING", hid, hid, "the WSL VM on %s is at %.0f%% of its memory cap" % (hid, pct),
                    "the VM holds %d MB of its %d MB cap (%s)%s. Cached files count toward that, so near the cap is a warning; with the host short of memory too it is the road to a hang" % (
                        mb, cap, cap_src, "; the host is short of memory as well" if short else ""),
                    evidence=ev, how=finding.how("a person at the machine", "Lowering the cap or the guest's load is a person's call; the Infirmary changes nothing, and the effective caps are in the evidence."),
                    recommendation="Find what fills the guest before it starts to stall; raise `memory=` in .wslconfig only if the host has room.", lk_kind="capacity"))
        parts.append("%s: VM %s%s%s" % (hid, "up, %d MB" % mb if vm and mb is not None else "up" if vm else "down",
                                         ", %.0f%% of cap" % pct if pct is not None else "", ", %.0f%% CPU" % cpu if cpu is not None else ""))
    return registry.found(findings, "; ".join(parts + notes), probed=[h["id"] for h, _ in rows], windows=doc)


# ---- 16. farm processes a cut-off job left behind ------------------------------------------------------------------------
def classify_leaks(lk, rows):
    """Split the probe's parentless processes into the ones that are farm leftovers and the ones that are somebody's own."""
    servers = set(x.lower() for x in lk["servers"])
    farm = set(x.lower() for x in lk["farm"])
    mine, other = [], []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "")).lower()
        if name in servers or (name in farm and _list(r.get("marks"))):
            mine.append(r)
        else:
            other.append(r)
    return mine, other


def check_leaks(ctx):
    hosts = windows_hosts(ctx)
    if not hosts:
        return registry.skip("no Windows host configured (doctor.hosts with os windows)")
    lk = ctx.d["windows"]["leaks"]
    rows, notes = _readable(ctx, hosts)
    if not rows:
        return registry.unknown("; ".join(notes) + " (its processes cannot be read)", probed=[])
    findings, parts, doc = [], [], {}
    for h, row in rows:
        hid = h["id"]
        leaks = row.get("leaks")
        if not isinstance(leaks, dict):
            notes.append("%s: the probe has no process inventory (an older probe)" % hid)
            continue
        listed = [r for r in _list(leaks.get("rows")) if isinstance(r, dict)]
        mine, other = classify_leaks(lk, listed)
        sess = [x for x in _list(leaks.get("sessions")) if isinstance(x, dict)]
        sess_n, sess_mb = sum(int(_num(x.get("n")) or 0) for x in sess), sum(int(_num(x.get("mb")) or 0) for x in sess)
        total_mb = sum(int(_num(r.get("ws_mb")) or 0) + int(_num(r.get("tree_mb")) or 0) for r in mine)
        doc[hid] = {"leaks": {"scanned": leaks.get("scanned"), "parentless_listed": len(listed), "reported": len(mine), "reported_mb": total_mb,
                              "left_alone": len(other), "orphan_sessions": sess_n, "pids": [r.get("pid") for r in mine][:20], "probe": row.get("v", 1)}}
        by = {}
        for r in mine:
            by.setdefault(str(r.get("name")), []).append(r)
        over_sess = sess_n >= lk["session_warn"]
        if not mine and not over_sess:
            parts.append("%s: %s process(es) scanned, none left without a live parent%s" % (hid, leaks.get("scanned"), "; %d unmarked parentless process(es) left alone" % len(other) if other else ""))
            continue
        ev = []
        for r in sorted(mine, key=lambda r: -(int(_num(r.get("ws_mb")) or 0) + int(_num(r.get("tree_mb")) or 0)))[:8]:
            ev.append(finding.fact("windows probe", "pid %s %s, %s old, %s MB%s, parent %s (pid %s)%s, command hash %s" % (
                r.get("pid"), r.get("name"), _age(r.get("age_min")), r.get("ws_mb"), " (+%s child process(es), %s MB)" % (r.get("tree_n"), r.get("tree_mb")) if r.get("tree_n") else "",
                "gone" if r.get("parent") == "dead" else "pid reused by a younger process", r.get("ppid"), "; marked by " + ", ".join(_list(r.get("marks"))) if _list(r.get("marks")) else "",
                r.get("cmd_sha") or "none"), "%s pid %s" % (hid, r.get("pid"))))
        if (_num(leaks.get("total")) or 0) > len(listed):
            ev.append(finding.fact("windows probe", "%d parentless process(es) of the listed names were found; the probe returns the %d largest" % (leaks.get("total"), len(listed)), hid))
        if over_sess:
            ev.append(finding.fact("windows probe", "%d session process(es) older than %dh with no live parent: %s" % (
                sess_n, lk["session_hours"], ", ".join("%d %s" % (x.get("n", 0), x.get("name")) for x in sess)), hid))
        why = []
        if mine:
            why.append("%s left running with no live parent for at least %d min, holding %.1f GB between them and their children" % (
                ", ".join("%d %s" % (len(v), k) for k, v in sorted(by.items())), lk["min_age_min"], total_mb / 1024.0))
        if over_sess:
            why.append("%d bash, sshd or powershell session process(es) older than %dh have lost their parent" % (sess_n, lk["session_hours"]))
        crit = total_mb >= lk["crit_mb"]
        findings.append(finding.make(
            "windows-leaks", 16, "leaked-farm-processes", hid, "CRITICAL" if crit else "WARNING", hid, hid,
            "%s holds farm processes with no live parent" % hid, "; ".join(why), evidence=ev,
            how=finding.how("the job that started them (or a person at the machine)", "Whether a job still needs one is the owner's to say. The Infirmary ends nothing on a Windows host; "
                            "ending one is done by pid and start time, never by name."),
            recommendation="Check that no live job uses these, then end them by identity (pid and start time) from the machine; unmarked processes are never listed here.",
            lk_kind="capacity", target={"host": hid, "processes": [{"pid": r.get("pid"), "name": r.get("name"), "born": r.get("born"), "cmd_sha": r.get("cmd_sha")} for r in mine[:20]]}))
        parts.append("%s: %d leaked process(es), %.1f GB" % (hid, len(mine), total_mb / 1024.0))
    if not doc:
        return registry.unknown("; ".join(notes), probed=[])
    return registry.found(findings, "; ".join(parts + notes), probed=[h["id"] for h, _ in rows], windows=doc)


def _age(minutes):
    m = _num(minutes)
    if m is None:
        return "an unknown time"
    return "%dh%02dm" % (m // 60, m % 60) if m >= 60 else "%dm" % m


# ---- 14. what a Windows host offers a job, against what it declares -----------------------------------------------------
def _ver(text):
    nums = re.findall(r"\d+", str(text or ""))
    return tuple(int(n) for n in nums[:4])


def shell_of(caps):
    """The login shell a job's command lands in: the registry's DefaultShell, else cmd.exe (what sshd uses when none is set)."""
    ds = str(caps.get("default_shell") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not ds:
        return "cmd"
    return {"powershell.exe": "powershell", "pwsh.exe": "pwsh", "cmd.exe": "cmd", "bash.exe": "bash"}.get(ds, ds)


def capabilities(row, login_profile_ok):
    caps = row.get("caps") if isinstance(row.get("caps"), dict) else {}
    pref = {}
    for x in _list(caps.get("gpu_pref")):
        if isinstance(x, dict):
            m = re.search(r"GpuPreference=(\d)", str(x.get("value")))
            pref[str(x.get("app"))] = GPU_PREF.get(m.group(1), "unknown") if m else "unknown"
    return {"login_shell": shell_of(caps), "launched_by": caps.get("launched_by") or None, "login_profile_ok": login_profile_ok, "powershell": caps.get("ps_version") or None,
            "gpu": [{"name": g.get("name")} for g in _list(caps.get("gpu")) if isinstance(g, dict)], "gpu_preference": pref,
            "tools": dict((k, v) for k, v in (caps.get("tools") or {}).items()) if isinstance(caps.get("tools"), dict) else {}, "desktop_sessions": caps.get("desktop_sessions")}


def drift(caps, expect):
    """-> [(what, declared, found)] for each declared capability the host does not offer."""
    out = []
    if "shell" in expect and str(expect["shell"]).lower() != caps["login_shell"]:
        out.append(("login shell", str(expect["shell"]), caps["login_shell"]))
    if expect.get("login_profile") == "clean" and caps["login_profile_ok"] is False:
        out.append(("login shell start-up", "no errors", "the shell's profile fails to load, so every job's output starts with an error"))
    for want in _list(expect.get("gpu")):
        if not any(str(want).lower() in str(g.get("name")).lower() for g in caps["gpu"]):
            out.append(("graphics adapter", str(want), ", ".join(str(g.get("name")) for g in caps["gpu"]) or "none reported"))
    for app, level in (expect.get("gpu_preference") or {}).items():
        want, have = GPU_WORD.get(str(level).lower(), str(level)), caps["gpu_preference"].get(str(app).lower())
        if have != want:
            out.append(("graphics preference for " + str(app), want, have or "none set (Windows picks the adapter itself)"))
    for tool, need in (expect.get("tools") or {}).items():
        if tool not in caps["tools"]:
            out.append(("tool " + tool, "present", "not in doctor.windows.capabilities.tools, so not looked up"))
            continue
        have = caps["tools"][tool]
        if have is None:
            out.append(("tool " + tool, "present", "not found"))
            continue
        m = re.match(r"^\s*>=\s*(.+)$", str(need)) if isinstance(need, str) else None
        if m and _ver(have) < _ver(m.group(1)):
            out.append(("tool " + tool, ">= " + m.group(1).strip(), have or "version unknown"))
    if expect.get("desktop") is True and not caps["desktop_sessions"]:
        out.append(("desktop session", "a signed-in desktop", "none (no explorer.exe in a user session)"))
    return out


PROFILE_ERROR = re.compile(r"cannot be loaded because running scripts is disabled|Microsoft\.PowerShell_profile|profile\.ps1", re.I)


def check_capabilities(ctx):
    hosts = windows_hosts(ctx)
    if not hosts:
        return registry.skip("no Windows host configured (doctor.hosts with os windows)")
    rows, notes = _readable(ctx, hosts)
    if not rows:
        return registry.unknown("; ".join(notes) + " (what it offers cannot be read)", probed=[])
    findings, parts, doc, checked = [], [], {}, []
    for h, row in rows:
        hid = h["id"]
        if not isinstance(row.get("caps"), dict):
            notes.append("%s: the probe carries no capabilities (an older probe)" % hid)
            continue
        res = hostmod.ssh_result(ctx, h)
        caps = capabilities(row, not bool(PROFILE_ERROR.search(res.err or "")))
        doc[hid] = {"capabilities": dict(caps, probe=row.get("v", 1))}
        expect = h.get("expect") if isinstance(h.get("expect"), dict) else None
        tools = ", ".join("%s %s" % (k, v if v else "present") for k, v in sorted(caps["tools"].items()) if v is not None)
        gpus = ", ".join(g["name"] for g in caps["gpu"] if g.get("name"))
        summary = "%s: shell %s%s, %s, tools: %s" % (hid, caps["login_shell"], " (its profile fails to load)" if caps["login_profile_ok"] is False else "",
                                                      "gpu: " + gpus if gpus else "no adapter reported", tools or "none found")
        if not expect:
            parts.append(summary + " (no expectations declared, drift not checked)")
            continue
        checked.append(hid)
        bad = drift(caps, expect)
        if not bad:
            parts.append(summary + " (as declared)")
            continue
        findings.append(finding.make(
            "windows-capabilities", 14, "capability-drift", hid, "WARNING", hid, hid, "%s no longer offers what it is declared to offer" % hid,
            "; ".join("%s: declared %s, found %s" % b for b in bad),
            evidence=[finding.fact("windows probe", summary, hid)] + [finding.fact("declared", "%s: declared %s, found %s" % b, hid) for b in bad[:6]],
            how=finding.how("a person at the machine", "Fix the host or change what it declares; the Infirmary changes neither."),
            recommendation="Send jobs that need these capabilities elsewhere until the host matches its declaration again.", lk_kind="machine"))
        parts.append(summary + " (%d drifted)" % len(bad))
    if not doc:
        return registry.unknown("; ".join(notes), probed=[])
    return registry.found(findings, "; ".join(parts + notes), probed=[h["id"] for h, _ in rows], windows=doc, drift_checked=checked)


SPECS = [registry.Spec("wsl-vm", 2, "WSL seen from Windows", check_wsl, remote=True),
         registry.Spec("windows-capabilities", 14, "Windows host capabilities", check_capabilities, remote=True),
         registry.Spec("windows-leaks", 16, "Leaked farm processes (Windows)", check_leaks, remote=True)]
