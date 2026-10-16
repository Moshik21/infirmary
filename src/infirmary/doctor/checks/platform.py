"""Catalogue 23 to 27: the Windows platform under the lanes. A Windows Update restart can install a firmware or platform driver
update that switches virtualization off in the firmware; the hypervisor then fails to launch at boot (Hyper-V-Hypervisor event
41), WSL 2 cannot start (`HCS_E_HYPERV_NOT_INSTALLED`) and every lane on the machine is gone until a person turns it back on.

One read-only PowerShell script (`windows/platform.ps1`) answers all five checks, once per tick and Windows side:

- this machine's own Windows side, when the Infirmary runs in a WSL guest (`powershell.exe` through interop) or on Windows itself;
- each Windows host in `doctor.hosts`, inside the one ssh probe `hung-host` already sends (the script is appended to it).

It reads CIM classes, a few named registry values and the System event log. It writes nothing, changes no firmware, boot, policy,
service or scheduled task setting, installs nothing and never calls `wsl.exe`. Times are compared on the Windows side's own
clock (the answer carries its `now`), never with this machine's.
"""
import json
import os
import re
import shutil
import time

from infirmary.doctor import finding, registry
from infirmary.doctor.checks import hosts as hostmod

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "INFIRMARY_PLATFORM "
IDS = ("firmware-virtualization", "hypervisor-launch", "pending-reboot", "update-restarts", "update-policy")
WSL_PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
HCS = re.compile(r"HCS_E_HYPERV_NOT_INSTALLED|0x80370102", re.I)
# programs that restart a machine for Windows Update or servicing; a person's restart comes from the shell's power menu
WU_PROGRAMS = ("mousocoreworker.exe", "usoclient.exe", "musnotification.exe", "musnotificationux.exe", "trustedinstaller.exe", "tiworker.exe",
               "wuauclt.exe", "sihclient.exe", "setuphost.exe")
PERSON_PROGRAMS = ("startmenuexperiencehost.exe", "shellexperiencehost.exe", "explorer.exe", "winlogon.exe", "logonui.exe")
REBOOT_SOURCES = {"windows-update": "Windows Update", "servicing": "Windows servicing"}   # the labels Harbor's own Windows beat uses
WEAK_SOURCES = {"file_renames": "files wait to be replaced at the next start", "update_exe_volatile": "an update executable asked for a restart",
                "computer_rename": "the computer name changes at the next start"}


# ---- reading the Windows side ----------------------------------------------------------------------------------------------
def enabled(ctx):
    off = set(ctx.d["disabled"])
    return any(i not in off for i in IDS)


def script(ctx):
    """The platform script with this run's settings written in (one whole number: how far back the event log is read)."""
    with open(os.path.join(os.path.dirname(HERE), "windows", "platform.ps1"), encoding="utf-8") as fh:
        text = fh.read()
    return text.replace("__LOOKBACK_H__", str(int(ctx.d["windows"]["platform"]["lookback_h"]))).strip()


def parse(out):
    row = None
    for ln in (out or "").splitlines():
        if ln.startswith(MARK):
            try:
                row = json.loads(ln[len(MARK):])
            except ValueError:
                row = None
    return row if isinstance(row, dict) else None


def powershell(ctx):
    """The PowerShell to ask: the configured one, else the one on PATH, else (in a WSL guest) Windows' own under /mnt/c."""
    p = ctx.d["windows"]["platform"]["powershell"]
    if p:
        return os.path.expanduser(p)
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    if ctx.osk == "wsl" and os.path.isfile(WSL_PS):
        return WSL_PS
    return None


def _local(ctx):
    """(row, why) for this machine's own Windows side, asked once per tick."""
    def go():
        ps = powershell(ctx)
        if not ps:
            return None, "no powershell.exe on PATH%s (set doctor.windows.platform.powershell)" % (" or at " + WSL_PS if ctx.osk == "wsl" else "")
        cmd, stdin = hostmod.powershell_stdin(script(ctx))
        res = ctx.run([ps] + cmd, timeout=ctx.d["windows"]["platform"]["timeout_s"], input_text=stdin)
        if res.missing or (res.rc not in (0, None) and not res.out.strip()):
            hint = ": Windows interop may be off in this guest (see the wsl-interop check)" if ctx.osk == "wsl" else ""
            return None, "%s could not be started%s (%s)" % (os.path.basename(ps), hint, hostmod._first_err(res.err) or ("not found" if res.missing else "exit %s" % res.rc))
        row = parse(res.out)
        if row is None:
            return None, "%s answered without a readable platform line (%s)" % (os.path.basename(ps), "timed out" if res.timed_out else hostmod._first_err(res.err) or "exit %s" % res.rc)
        return row, ""
    return ctx.once(("platform-local",), go)


def subjects(ctx):
    """Every Windows side to judge: [{id, seen_from, row, why, host, expect, guest_err}]. Never raises, never asks twice."""
    out, ids = [], set()
    pc = ctx.d["windows"]["platform"]
    if pc["local"] == "auto" and ctx.osk in ("wsl", "windows"):
        row, why = _local(ctx)
        sid = pc["local_id"] or ctx.host_id
        if ctx.osk == "wsl":
            expect = (True, "this host is a WSL guest") if pc["wsl_expected"] is not False else (False, "doctor.windows.platform.wsl_expected is false")
        else:
            expect = _expected_by_row(row, pc["wsl_expected"])
        out.append({"id": sid, "seen_from": "local", "row": row, "why": why, "host": None, "expect": expect, "guest_err": ""})
        ids.add(sid)
    for h in [x for x in ctx.d["hosts"] if x.get("os") == "windows"]:
        if h["id"] in ids:
            continue
        res = hostmod.ssh_result(ctx, h)
        row, why = None, ""
        if not (res.ok and "INFIRMARY" in res.out):
            _, why = hostmod.windows_row(ctx, h)
        else:
            row = parse(res.out)
            if row is None:
                why = "%s answered the probe without a platform block (an older Infirmary's probe)" % h["id"]
        exp = h.get("expect") if isinstance(h.get("expect"), dict) else {}
        if h.get("guest"):
            expect = (True, "a guest is configured for it (%s)" % h["guest"].get("ssh"))
        elif isinstance(exp.get("wsl2"), bool):
            expect = (exp["wsl2"], "its expect block says wsl2: %s" % str(exp["wsl2"]).lower())
        else:
            expect = _expected_by_row(row, None)
        gerr = ""
        if h.get("guest") and row is not None:
            answered, err = hostmod.guest_answers(ctx, h)
            gerr = err if answered is False else ""
        out.append({"id": h["id"], "seen_from": "ssh", "row": row, "why": why, "host": h, "expect": expect, "guest_err": gerr})
    return out


def _expected_by_row(row, override):
    if isinstance(override, bool):
        return override, "doctor.windows.platform.wsl_expected is %s" % str(override).lower()
    n = _num(((row or {}).get("distros") or {}).get("v2"))
    if n is None:
        return None, "whether a WSL 2 distribution is registered could not be read"
    return (n > 0), ("%d WSL 2 distribution(s) registered" % n if n else "no WSL 2 distribution registered")


# ---- small readers -----------------------------------------------------------------------------------------------------------
def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _list(x):
    return x if isinstance(x, list) else ([] if x in (None, "") else [x])


def _events(row, key):
    return [e for e in _list(((row.get("events") or {}).get(key))) if isinstance(e, dict) and _num(e.get("at")) is not None]


def when(row, at):
    """An event time on the Windows side's own clock and zone: `2026-10-10 04:32 (UTC-07:00)`."""
    off = _num(row.get("tz_offset_min")) or 0
    return "%s (UTC%s%02d:%02d)" % (time.strftime("%Y-%m-%d %H:%M", time.gmtime(at + off * 60)), "-" if off < 0 else "+", abs(off) // 60, abs(off) % 60)


def ago(row, at):
    s = max(0, int((_num(row.get("now")) or at) - at))
    return "%dh%02dm ago" % (s // 3600, s % 3600 // 60) if s >= 3600 else "%dm ago" % (s // 60)


def local_hour(row, at):
    return int(((at + (_num(row.get("tz_offset_min")) or 0) * 60) % 86400) // 3600)


def active_hours(row):
    """(start, end, source): the hours Windows will not restart in on its own; a policy wins over the user's setting; 8 to 17 by default."""
    pol = (row.get("policy") or {})
    po, ux = pol.get("policy") or {}, pol.get("ux") or {}
    if po.get("SetActiveHours") == 1 and _num(po.get("ActiveHoursStart")) is not None and _num(po.get("ActiveHoursEnd")) is not None:
        return int(po["ActiveHoursStart"]), int(po["ActiveHoursEnd"]), "policy"
    if _num(ux.get("ActiveHoursStart")) is not None and _num(ux.get("ActiveHoursEnd")) is not None:
        return int(ux["ActiveHoursStart"]), int(ux["ActiveHoursEnd"]), "user setting"
    return 8, 17, "Windows' default"


def inside(hour, start, end):
    return start <= hour < end if start < end else (hour >= start or hour < end) if start != end else True


def update_class(title):
    t = str(title or "").lower()
    if re.search(r"firmware|\bbios\b|uefi|capsule", t):
        return "firmware"
    if re.search(r"\bdriver\b", t) or re.match(r"^[^-]+ - [a-z]+ - ", t):
        return "driver"
    return "other"


def initiator(r):
    """'windows-update', 'person' or 'other' for a requested restart (User32 1074)."""
    by = str(r.get("by") or "").lower()
    if by in PERSON_PROGRAMS:
        return "person"
    try:
        code = int(str(r.get("code") or "0"), 16)
    except ValueError:
        code = 0
    if by in WU_PROGRAMS or (code & 0x80000000 and (code >> 16) & 0xFF == 0x02 and by not in ("shutdown.exe",)):
        return "windows-update"
    return "other"


def updates_around(ctx, row, at):
    """Updates installed (or failed) in the hours before a restart and the minutes after it (a restart finishes the install)."""
    win = ctx.d["windows"]["platform"]["update_window_h"] * 3600
    return [u for u in _events(row, "updates") if at - win <= u["at"] <= at + 900]


def _titles(ups, kinds=("firmware", "driver")):
    keep = [u for u in ups if update_class(u.get("title")) in kinds]
    names = []
    for u in sorted(keep, key=lambda u: u["at"]):
        t = "%s%s" % (u.get("title"), " (failed, %s)" % u.get("err") if u.get("id") == 20 else "")
        if t not in names:
            names.append(t)
    return names


def summary(s):
    """The `platform` part of doctor.json's windows block, the same for every check of one subject."""
    row = s["row"]
    return {"seen_from": s["seen_from"], "probe": row.get("v"), "now_epoch": row.get("now"), "boot_epoch": row.get("boot"), "tz_offset_min": row.get("tz_offset_min"),
            "wsl_expected": s["expect"][0], "wsl_expected_because": s["expect"][1]}


def _judge(ctx, fn, what):
    """Run fn(subject) -> (findings, part, doc[, known]) on every subject that answered. A subject that did not answer, or whose part could not
    be read (known False), is a note; with nothing known and nothing found the check is unknown, never ok."""
    subs = subjects(ctx)
    if not subs:
        return registry.skip("no Windows side to read: this host is %s, no Windows host is configured (doctor.hosts with os windows)%s" % (
            ctx.osk, " and doctor.windows.platform.local is off" if ctx.d["windows"]["platform"]["local"] != "auto" else ""))
    findings, parts, notes, docs = [], [], [], {}
    for s in subs:
        if s["row"] is None:
            notes.append("%s: %s" % (s["id"], s["why"]))
            continue
        res = fn(ctx, s)
        fs, part, doc = res[:3]
        findings += fs
        (parts if (res[3] if len(res) > 3 else True) else notes).append("%s: %s" % (s["id"], part))
        docs[s["id"]] = dict(doc, platform=summary(s))
    if not parts and not findings:
        return registry.unknown("; ".join(notes) + " (%s cannot be read)" % what, windows=docs)
    return registry.found(findings, "; ".join(parts + notes), windows=docs)


def _how(note):
    return finding.how("a person at the machine", note + " The Infirmary changes no firmware, boot, policy, service or scheduled-task setting and installs nothing.")


# ---- 23. virtualization off in firmware while a WSL 2 guest is expected -------------------------------------------------------
def _firmware(ctx, s):
    row, sid = s["row"], s["id"]
    v = row.get("virt") or {}
    hv, fw, vmp = v.get("hypervisor_present"), v.get("firmware_enabled"), v.get("vm_platform")
    expected, because = s["expect"]
    doc = {"virtualization": {"hypervisor_present": hv, "firmware_enabled": fw, "vbs_status": v.get("vbs_status"),
                              "vm_platform": {1: "enabled", 2: "disabled", 3: "absent"}.get(vmp), "wsl2_distributions": (row.get("distros") or {}).get("v2")}}
    ev = [finding.fact("windows platform", "hypervisor present: %s; virtualization enabled in firmware (as the processor reports it): %s; Virtual Machine Platform feature: %s" % (
        _yn(hv), _yn(fw), doc["virtualization"]["vm_platform"] or "not readable"), sid),
          finding.fact("windows platform", "WSL 2 expected: %s (%s); last boot %s" % (_yn(expected), because, when(row, row["boot"]) if _num(row.get("boot")) else "unknown"), sid)]
    if s["guest_err"]:
        ev.append(finding.fact("ssh", "the guest does not answer: %s" % s["guest_err"], (s["host"] or {}).get("guest", {}).get("ssh")))
    if hv is True:
        return [], "the hypervisor is running (virtualization was on in firmware at boot)", doc
    if hv is not False:
        return [], "whether a hypervisor runs could not be read", doc, False
    if expected is None:
        return [], "no hypervisor, virtualization in firmware %s, and whether a WSL 2 guest is expected could not be read" % ("off" if fw is False else "on" if fw else "unknown"), doc, False
    if not expected:
        return [], "no hypervisor, virtualization in firmware %s; no WSL 2 guest is expected (%s)" % ("off" if fw is False else "on" if fw else "unknown", because), doc
    hcs = " WSL says so in its own words: HCS_E_HYPERV_NOT_INSTALLED." if HCS.search(s["guest_err"] or "") else ""
    if fw is False:
        return [finding.make(
            "firmware-virtualization", 23, "firmware-virtualization-off", sid, "CRITICAL", sid, sid, "virtualization is off in the firmware of %s" % sid,
            "no hypervisor is running and the processor reports virtualization (Intel VT-x / AMD SVM) disabled in firmware while a WSL 2 guest is expected (%s): "
            "WSL 2 cannot start, so no lane can run there.%s A firmware or platform driver update installed by Windows Update can switch it off at a restart" % (because, hcs),
            evidence=ev, how=_how("Virtualization is switched back on in the firmware setup at boot, by a person at the machine."),
            recommendation="Record the lanes this machine held. At the machine: restart into the firmware setup (UEFI/BIOS), enable Intel Virtualization Technology (VT-x) "
                           "or AMD SVM, save and boot; `systeminfo` then says a hypervisor has been detected and WSL starts. Relaunch the lanes afterwards.",
            lk_kind="machine", target={"host": sid, "hypervisor_present": hv, "firmware_enabled": fw})], "virtualization off in firmware, WSL 2 cannot start", doc
    if fw is True:
        return [finding.make(
            "firmware-virtualization", 23, "hypervisor-not-running", sid, "CRITICAL", sid, sid, "the hypervisor is not running on %s" % sid,
            "virtualization is on in firmware but no hypervisor is running while a WSL 2 guest is expected (%s): the Virtual Machine Platform feature is %s, or the "
            "boot configuration does not launch the hypervisor (hypervisorlaunchtype). WSL 2 cannot start.%s" % (because, doc["virtualization"]["vm_platform"] or "of unknown state", hcs),
            evidence=ev, how=_how("Turning the Virtual Machine Platform feature or the hypervisor launch back on needs an administrator and a restart."),
            recommendation="At the machine, as an administrator: check `bcdedit /enum` for hypervisorlaunchtype and the Virtual Machine Platform feature, fix what is off, "
                           "restart once the lanes are recorded.", lk_kind="machine", target={"host": sid, "hypervisor_present": hv, "firmware_enabled": fw})], "no hypervisor although firmware allows it", doc
    return [], "no hypervisor, and the firmware setting could not be read", doc, False


def _yn(x):
    return "yes" if x is True else "no" if x is False else "unknown"


def check_firmware(ctx):
    return _judge(ctx, _firmware, "the virtualization state")


# ---- 24. hypervisor launch failures (Hyper-V-Hypervisor 41) ------------------------------------------------------------------
def _before(row, at):
    """The restart (requested or not) that led to the boot at `at`: the newest one before it."""
    req = [dict(r, what="requested") for r in _events(row, "restarts") if r["at"] <= at]
    un = [dict(r, what="unexpected") for r in _events(row, "unexpected") if r["at"] <= at]
    return max(req + un, key=lambda r: r["at"]) if req + un else None


def restart_text(row, r):
    if r.get("what") == "unexpected":
        return "an unexpected restart (%s) at %s" % ("EventLog 6008" if r.get("id") == 6008 else "Kernel-Power 41", when(row, r["at"]))
    who = initiator(r)
    return "a restart by %s%s at %s (%s)" % (r.get("by") or "an unnamed program", " for Windows Update" if who == "windows-update" else " from the power menu" if who == "person" else "",
                                           when(row, r["at"]), r.get("reason") or "no reason given")


def _hypervisor(ctx, s):
    row, sid = s["row"], s["id"]
    if not (row.get("events") or {}).get("read"):
        return [], "the System event log could not be read", {"hypervisor": None}, False
    pc = ctx.d["windows"]["platform"]
    now, boot = _num(row.get("now")), _num(row.get("boot"))
    hv = _events(row, "hypervisor")
    fails = sorted([e for e in hv if e.get("id") == 41], key=lambda e: e["at"])
    starts = sorted([e for e in hv if e.get("id") == 1], key=lambda e: e["at"])
    vsm = [t for t in _list((row.get("events") or {}).get("vsm_failed")) if _num(t) is not None]
    present = (row.get("virt") or {}).get("hypervisor_present")
    doc = {"hypervisor": {"launch_failures": [e["at"] for e in fails], "last_start": starts[-1]["at"] if starts else None, "vsm_failures": len(vsm)}}
    current = [e for e in fails if boot is not None and e["at"] >= boot - 120] if present is not True else []
    recent = [e for e in fails if now is not None and e["at"] >= now - pc["hypervisor_recent_h"] * 3600]
    if not current and not recent:
        return [], "no hypervisor launch failure in the last %dh%s" % (pc["hypervisor_recent_h"], "; it started %s" % ago(row, starts[-1]["at"]) if starts else ""), doc
    first = (current or recent)[0]
    led = _before(row, first["at"])
    ups = updates_around(ctx, row, led["at"]) if led else []
    ev = [finding.fact("System log", "Hyper-V-Hypervisor 41 (hypervisor launch failed; VMX not present or not enabled in firmware) at %s" % ", ".join(when(row, e["at"]) for e in (current or recent)[:6]), sid)]
    if starts:
        ev.append(finding.fact("System log", "Hyper-V-Hypervisor 1 (hypervisor started) last at %s" % when(row, starts[-1]["at"]), sid))
    if vsm:
        ev.append(finding.fact("System log", "Kernel-Boot 124 (virtualization-based security found no hypervisor) at %d boot(s)" % len(vsm), sid))
    chain = ""
    if led:
        ev.append(finding.fact("System log", "the first failed boot followed %s" % restart_text(row, led), sid))
        chain = "; the first failed boot followed %s" % restart_text(row, led)
        names = _titles(ups)
        if names:
            ev.append(finding.fact("System log", "driver or firmware updates around that restart: %s" % "; ".join(names[:6]), sid))
            chain += ", which came with %d driver or firmware update(s) (%s)" % (len(names), "; ".join(names[:3]))
    if s["guest_err"] and HCS.search(s["guest_err"]):
        ev.append(finding.fact("ssh", "the guest says: %s" % s["guest_err"], (s["host"] or {}).get("guest", {}).get("ssh")))
    if current:
        return [finding.make(
            "hypervisor-launch", 24, "hypervisor-launch-failed", sid, "CRITICAL", sid, sid, "the hypervisor failed to launch at this boot of %s" % sid,
            "Hyper-V-Hypervisor event 41 at boot (%s): virtualization is not available, so WSL 2 cannot start (HCS_E_HYPERV_NOT_INSTALLED) and every lane on the "
            "machine is down%s" % (when(row, current[-1]["at"]), chain), evidence=ev,
            how=_how("The usual cause is virtualization switched off in the firmware (often by a firmware or platform driver update); it is switched back on in the firmware setup."),
            recommendation="Record the lanes, then at the machine enable Intel VT-x or AMD SVM in the firmware setup and boot; see the firmware-virtualization finding and the "
                           "diagnosis recipe in docs/windows-platform.md.", lk_kind="machine", target={"host": sid, "failed_at": [e["at"] for e in current]})], "the hypervisor failed to launch at this boot", doc
    since = [e for e in starts if e["at"] > recent[-1]["at"]]
    return [finding.make(
        "hypervisor-launch", 24, "hypervisor-launch-failed-recently", sid, "WARNING", sid, sid, "the hypervisor of %s failed to launch %d time(s) in the last %dh" % (sid, len(recent), pc["hypervisor_recent_h"]),
        "Hyper-V-Hypervisor event 41 at %s: while it lasted WSL 2 could not start and every lane there was down%s. It is running again%s" % (
            ", ".join(when(row, e["at"]) for e in recent[:4]), chain, " since %s" % when(row, since[0]["at"]) if since else ""), evidence=ev,
        how=_how("Whoever turned virtualization back on knows what reset it; the restart and the updates in the evidence are where to look."),
        recommendation="Check that every lane of that window was relaunched. If a driver or firmware update reset the firmware setting, expect it again at the next such "
                       "update: see the update-policy check and docs/windows-platform.md.", lk_kind="machine", target={"host": sid, "failed_at": [e["at"] for e in recent]})], \
        "%d launch failure(s) in the last %dh, running again" % (len(recent), pc["hypervisor_recent_h"]), doc


def check_hypervisor(ctx):
    return _judge(ctx, _hypervisor, "the hypervisor's launch events")


# ---- 25. a restart pending (and the fact Harbor reads) ----------------------------------------------------------------------
def reboot_fact(row):
    """The pending-restart fact in the shape a Harbor `harbor.beat/1` record carries: {pending, due_epoch, source}. Windows does not publish
    when it will restart, so `due_epoch` is null; `pending` is null when the keys could not be read."""
    rb = row.get("reboot") or {}
    if not rb.get("read"):
        return {"pending": None, "due_epoch": None, "source": None}
    strong = [x for x in _list(rb.get("strong")) if x in REBOOT_SOURCES]
    return {"pending": bool(strong), "due_epoch": None, "source": REBOOT_SOURCES[strong[0]] if strong else None}


def _pending(ctx, s):
    row, sid = s["row"], s["id"]
    fact = reboot_fact(row)
    rb = row.get("reboot") or {}
    weak = [x for x in _list(rb.get("weak")) if x in WEAK_SOURCES]
    boot = _num(row.get("boot"))
    staged = [u for u in _events(row, "updates") if u.get("id") == 19 and boot is not None and u["at"] >= boot]
    names = _titles(staged)
    start, end, src = active_hours(row)
    doc = {"reboot": fact, "reboot_detail": {"sources": [REBOOT_SOURCES[x] for x in _list(rb.get("strong")) if x in REBOOT_SOURCES], "weak": weak,
                                             "staged_driver_updates": names, "active_hours": [start, end], "active_hours_source": src}}
    if fact["pending"] is None:
        return [], "the restart keys could not be read", doc, False
    soft = "; softer signs: %s" % ", ".join(WEAK_SOURCES[x] for x in weak) if weak else ""
    if not fact["pending"]:
        return [], "no restart pending (Windows Update and servicing keys absent)%s" % soft, doc
    ev = [finding.fact("registry", "restart pending, set by %s" % ", ".join(doc["reboot_detail"]["sources"]), sid),
          finding.fact("registry", "active hours %02d:00 to %02d:00 (%s): outside them Windows may restart on its own" % (start, end, src), sid)]
    if names:
        ev.append(finding.fact("System log", "driver or firmware updates installed since the last boot, applied at the restart: %s" % "; ".join(names[:6]), sid))
    if weak:
        ev.append(finding.fact("registry", "softer signs: %s" % ", ".join(WEAK_SOURCES[x] for x in weak), sid))
    risky = " %d driver or firmware update(s) installed since the last boot take effect at that restart (%s): one of those can switch virtualization off in firmware, " \
            "so plan for a person who can reach the firmware setup." % (len(names), "; ".join(names[:3])) if names else ""
    return [finding.make(
        "pending-reboot", 25, "reboot-pending", sid, "WARNING", sid, sid, "%s has a restart pending" % sid,
        "%s waits for a restart; outside the active hours (%02d:00 to %02d:00, %s) Windows may restart on its own and every lane on the machine stops.%s" % (
            " and ".join(doc["reboot_detail"]["sources"]), start, end, src, risky), evidence=ev,
        how=_how("When to restart is a person's call: drain the machine (Harbor reads this fact), let running work finish, then restart while someone watches."),
        recommendation="Drain this machine before the restart and restart it deliberately, inside a window you choose; doctor.json carries the fact as "
                       "windows.<host>.reboot for a pool to read.", lk_kind="machine", target={"host": sid, "reboot": fact})], \
        "restart pending (%s)%s" % (", ".join(doc["reboot_detail"]["sources"]), ", with %d driver update(s) staged" % len(names) if names else ""), doc


def check_pending(ctx):
    return _judge(ctx, _pending, "a pending restart")


# ---- 26. recent Windows Update and unexpected restarts ----------------------------------------------------------------------
def _restarts(ctx, s):
    row, sid = s["row"], s["id"]
    if not (row.get("events") or {}).get("read"):
        return [], "the System event log could not be read", {"restarts": None}, False
    pc = ctx.d["windows"]["platform"]
    now = _num(row.get("now")) or 0
    cut = now - pc["restart_recent_h"] * 3600
    start, end, src = active_hours(row)
    req = sorted([r for r in _events(row, "restarts") if r["at"] >= cut], key=lambda r: r["at"])
    un = sorted([r for r in _events(row, "unexpected") if r["at"] >= cut], key=lambda r: r["at"])
    wu = [r for r in req if initiator(r) == "windows-update"]
    fails = sorted(e["at"] for e in _events(row, "hypervisor") if e.get("id") == 41)
    rows = []
    for r in wu:
        names = _titles(updates_around(ctx, row, r["at"]))
        broke = [t for t in fails if t > r["at"] and (not [x for x in req + un if r["at"] < x["at"] < t])]   # the very next boot failed
        rows.append({"at": r["at"], "by": r.get("by"), "reason": r.get("reason"), "inside_active_hours": inside(local_hour(row, r["at"]), start, end),
                     "driver_updates": names, "hypervisor_failed_after": bool(broke)})
    doc = {"restarts": {"window_h": pc["restart_recent_h"], "windows_update": rows, "unexpected": [r["at"] for r in un],
                        "other": [{"at": r["at"], "by": r.get("by"), "initiator": initiator(r)} for r in req if initiator(r) != "windows-update"]}}
    findings = []
    if rows:
        ev = []
        for x in rows[-6:]:
            ev.append(finding.fact("System log", "User32 1074: %s restarted the machine at %s (%s), %s active hours%s%s" % (
                x["by"], when(row, x["at"]), x["reason"] or "no reason given", "inside" if x["inside_active_hours"] else "outside",
                "; driver or firmware updates: " + "; ".join(x["driver_updates"][:4]) if x["driver_updates"] else "",
                "; the next boot came up without the hypervisor (event 41)" if x["hypervisor_failed_after"] else ""), sid))
        last = rows[-1]
        drv = sum(len(x["driver_updates"]) for x in rows)
        broke = any(x["hypervisor_failed_after"] for x in rows)
        findings.append(finding.make(
            "update-restarts", 26, "update-restart", sid, "WARNING", sid, sid, "Windows Update restarted %s %d time(s) in the last %dh" % (sid, len(rows), pc["restart_recent_h"]),
            "the last at %s (%s), %s the active hours %02d:00 to %02d:00 (%s); every lane on the machine stopped%s%s" % (
                when(row, last["at"]), ago(row, last["at"]), "inside" if last["inside_active_hours"] else "outside", start, end, src,
                "; %d driver or firmware update(s) came with it" % drv if drv else "",
                "; the boot after it came up without the hypervisor (see hypervisor-launch)" if broke else ""), evidence=ev,
            how=_how("Whether driver updates come through Windows Update, and when it may restart, is the machine owner's policy; the update-policy check shows it."),
            recommendation="Check that every lane the restart stopped was relaunched. On a machine that runs lanes around the clock, consider excluding drivers from quality "
                           "updates (policy ExcludeWUDriversInQualityUpdate) and taking platform driver and firmware updates by hand, with a person at the machine; "
                           "drain before restarts that are announced (pending-reboot).", lk_kind="machine", target={"host": sid, "restarts": [x["at"] for x in rows]}))
    if un:
        findings.append(finding.make(
            "update-restarts", 26, "unexpected-restart", sid, "WARNING", sid, sid, "%s restarted unexpectedly %d time(s) in the last %dh" % (sid, len(un), pc["restart_recent_h"]),
            "the system stopped without a clean shutdown (%s), the last at %s; every lane on the machine stopped" % (
                ", ".join(sorted(set("EventLog 6008" if r.get("id") == 6008 else "Kernel-Power 41" for r in un))), when(row, un[-1]["at"])),
            evidence=[finding.fact("System log", "%s at %s" % ("EventLog 6008" if r.get("id") == 6008 else "Kernel-Power 41", when(row, r["at"])), sid) for r in un[-6:]],
            how=_how("Power loss, a crash or a hard reset: the machine's own reliability history says which."),
            recommendation="Check that the lanes were relaunched; look at the machine's reliability history for the cause.", lk_kind="machine",
            target={"host": sid, "unexpected": [r["at"] for r in un]}))
    if not findings:
        other = doc["restarts"]["other"]
        return [], "no Windows Update or unexpected restart in the last %dh%s" % (pc["restart_recent_h"], "; %d restart(s) asked for otherwise (%s)" % (
            len(other), ", ".join(sorted(set(str(o["by"]) for o in other)))) if other else ""), doc
    return findings, "%d Windows Update restart(s), %d unexpected, in the last %dh" % (len(rows), len(un), pc["restart_recent_h"]), doc


def check_restarts(ctx):
    return _judge(ctx, _restarts, "its restarts")


# ---- 27. the update policy, as information -----------------------------------------------------------------------------------
def policy(row):
    pol = row.get("policy") or {}
    po, au, ux, dr, pz = (pol.get(k) or {} for k in ("policy", "au", "ux", "driver_search", "paused"))
    start, end, src = active_hours(row)
    excl = po.get("ExcludeWUDriversInQualityUpdate")
    search = dr.get("SearchOrderConfig")
    if excl == 1:
        drivers = "excluded from Windows Update by policy"
    elif search == 0:
        drivers = "not fetched for new devices, not excluded by policy"
    else:
        drivers = "come through Windows Update"
    paused = [k for k in ("PausedQualityStatus", "PausedFeatureStatus") if pz.get(k) == 1]
    return {"active_hours": [start, end], "active_hours_source": src, "smart_active_hours": {1: True, 0: False}.get(ux.get("SmartActiveHoursState")),
            "drivers": drivers, "exclude_drivers_policy": excl, "driver_search": search,
            "defer_quality_days": po.get("DeferQualityUpdatesPeriodInDays") if po.get("DeferQualityUpdates") == 1 else 0,
            "defer_feature_days": po.get("DeferFeatureUpdatesPeriodInDays") if po.get("DeferFeatureUpdates") == 1 else 0,
            "paused": [p.replace("Paused", "").replace("Status", "").lower() for p in paused],
            "pause_until": ux.get("PauseQualityUpdatesEndTime") or ux.get("PauseUpdatesExpiryTime") or None,
            "no_auto_restart_with_user_signed_in": {1: True, 0: False}.get(au.get("NoAutoRebootWithLoggedOnUsers")),
            "auto_update": {1: "notify only", 2: "notify to download", 3: "download, notify to install", 4: "install on a schedule", 5: "local admin chooses", 7: "notify to restart"}.get(
                au.get("AUOptions"), "off by policy" if au.get("NoAutoUpdate") == 1 else "default (restarts outside active hours)"),
            "deadline_quality_days": po.get("ConfigureDeadlineForQualityUpdates"), "deadline_no_auto_restart": {1: True, 0: False}.get(po.get("ConfigureDeadlineNoAutoReboot"))}


def _policy(ctx, s):
    row = s["row"]
    if not isinstance(row.get("policy"), dict):
        return [], "the update policy could not be read", {"update_policy": None}, False
    p = policy(row)
    text = "active hours %02d-%02d (%s%s); drivers %s; %s; %s%s; updates: %s" % (
        p["active_hours"][0], p["active_hours"][1], p["active_hours_source"], ", automatic" if p["smart_active_hours"] else "", p["drivers"],
        "quality updates deferred %s day(s)" % p["defer_quality_days"] if p["defer_quality_days"] else "no deferral", "paused (%s)" % ", ".join(p["paused"]) if p["paused"] else "not paused",
        "; no automatic restart while someone is signed in" if p["no_auto_restart_with_user_signed_in"] else "", p["auto_update"])
    return [], text, {"update_policy": p}


def check_policy(ctx):
    return _judge(ctx, _policy, "the update policy")


SPECS = [registry.Spec("firmware-virtualization", 23, "Virtualization in firmware (WSL 2)", check_firmware, remote=True),
         registry.Spec("hypervisor-launch", 24, "Hypervisor launch failures", check_hypervisor, remote=True),
         registry.Spec("pending-reboot", 25, "Windows restart pending", check_pending, remote=True),
         registry.Spec("update-restarts", 26, "Windows Update restarts", check_restarts, remote=True),
         registry.Spec("update-policy", 27, "Windows Update policy (information)", check_policy, remote=True)]
