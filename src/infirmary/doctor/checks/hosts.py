"""Catalogue 2 (a hung WSL or VM, a host out of memory, no reply on ssh) and 3 (a dead remote channel's control socket).

Other hosts are probed read-only over ssh with `BatchMode`, a connect timeout, no ControlPath (so no master is left
behind) and a hard timeout. A Windows host is asked one question in PowerShell (`windows/probe.ps1`, sent as an
encoded command, nothing is written there): free memory, commit, the VM's working set, the WSL caps, old leaked
processes. A host that stops answering is told apart in two steps: TCP accepted but sshd silent is a hung host;
nothing accepting is an unreachable one. The Infirmary never calls `wsl.exe` (a hung WSL hangs it too, and a probe
that hangs adds to the load of a host that is already sick).
"""
import base64
import glob
import json
import os
import re
import socket

from infirmary.doctor import finding, registry, sshclass

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "infirmary-ok"


def _ssh_base(ctx):
    # LogLevel=INFO: a quieter level in the operator's ssh config would hide the server's banner, where an additional-check prompt is printed
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=%d" % ctx.d["ssh_timeout_s"], "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "LogLevel=INFO"]


def _endpoint(ctx, alias):
    res = ctx.run(["ssh", "-G", alias], timeout=5)
    host, port = None, 22
    for row in res.out.splitlines():
        k, _, v = row.partition(" ")
        if k == "hostname":
            host = v.strip()
        elif k == "port" and v.strip().isdigit():
            port = int(v.strip())
    return host, port


def _tcp_stage(host, port, secs):
    """('closed'|'silent'|'banner', text): did the port accept, and did sshd greet us."""
    if not host:
        return "closed", "no address for the alias"
    try:
        s = socket.create_connection((host, port), timeout=secs)
    except (OSError, socket.timeout) as exc:
        return "closed", "tcp connect failed: %s" % (exc.strerror if hasattr(exc, "strerror") and exc.strerror else exc)
    try:
        s.settimeout(secs)
        try:
            data = s.recv(64)
        except (OSError, socket.timeout):
            return "silent", "tcp accepted but no ssh banner within %ds" % secs
        return ("banner", data.decode("utf-8", "replace").strip()[:40]) if data.startswith(b"SSH-") else ("silent", "tcp accepted, no ssh banner")
    finally:
        s.close()


def _ps_list(words, lower=False):
    """A PowerShell array literal. Each word passed config's plain-alphabet check (no quote can occur), so nothing is escaped."""
    return "@(" + ",".join("'%s'" % (w.lower() if lower else w) for w in words) + ")"


def windows_script(ctx):
    """The probe script with this run's settings written in. The settings are lists of plain names (`core.config` checks the alphabet)."""
    w = ctx.d["windows"]
    lk = w["leaks"]
    with open(os.path.join(os.path.dirname(HERE), "windows", "probe.ps1"), encoding="utf-8") as fh:
        script = fh.read()
    subs = {"__HOURS__": str(int(w["old_process_hours"])), "__MIN_AGE_MIN__": str(int(lk["min_age_min"])), "__SESSION_HOURS__": str(int(lk["session_hours"])),
            "__LEAK_NAMES__": _ps_list(list(lk["servers"]) + list(lk["farm"]), lower=True), "__MARKERS__": _ps_list(lk["farm_markers"]),
            "__SESSION_NAMES__": _ps_list(lk["sessions"], lower=True), "__TOOLS__": _ps_list(w["capabilities"]["tools"])}
    for key, value in subs.items():
        script = script.replace(key, value)
    from infirmary.doctor.checks import platform     # catalogue 23 to 27 ride on the same probe: one ssh call per host and tick
    if platform.enabled(ctx):
        script = script.strip() + "\n" + platform.script(ctx)
    return script.strip()


def powershell_stdin(script):
    """(argv tail, stdin) to run a script in `powershell -Command -`. The script is longer than a command line may be (cmd.exe stops
    at 8191 characters) and PowerShell reads a multi-line block from standard input line by line, so the script goes in as ONE
    line: base64 text that the shell decodes and runs in memory. Nothing is written to disk."""
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return ["-NoProfile", "-NonInteractive", "-Command", "-"], "iex ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')))\n" % b64


def _windows_cmd(ctx):
    """(remote command, stdin) for a Windows host over ssh: see `powershell_stdin`."""
    argv, stdin = powershell_stdin(windows_script(ctx))
    return "powershell " + " ".join(argv), stdin


def _first_err(text):
    for row in (text or "").splitlines():
        row = row.strip()
        if row and not row.startswith("**") and "post-quantum" not in row and "store now" not in row and "openssh.com" not in row:
            return row[:160]
    return ""


def ssh_result(ctx, h):
    """The one ssh probe of a host this tick (shared by every check that reads it): the ssh Result."""
    def go():
        alias = h["ssh"]
        if h.get("os", "linux") == "windows":
            cmd, stdin = _windows_cmd(ctx)
            from infirmary.doctor.checks import platform
            return ctx.run(_ssh_base(ctx) + [alias, cmd], timeout=ctx.d["ssh_timeout_s"] + 25 + (15 if platform.enabled(ctx) else 0), input_text=stdin)
        return ctx.run(_ssh_base(ctx) + [alias, "echo " + MARK], timeout=ctx.d["ssh_timeout_s"] + 8)
    return ctx.once(("ssh-probe", h["id"]), go)


def parse_windows(out):
    """The probe's `INFIRMARY {json}` line as a dict, or None. Probe version 1 lacks `v`, `caps`, `leaks` and the VM's CPU."""
    row = None
    for ln in (out or "").splitlines():
        if ln.startswith("INFIRMARY "):
            try:
                row = json.loads(ln[len("INFIRMARY "):])
            except ValueError:
                row = None
    return row if isinstance(row, dict) else None


def windows_row(ctx, h):
    """(row, why): the Windows host's probe answer, or None and the reason it is not there. Never raises, never asks twice."""
    res = ssh_result(ctx, h)
    if not (res.ok and "INFIRMARY " in res.out):
        return None, "%s did not answer the probe (%s)" % (h["id"], _first_err(res.err) or ("timed out" if res.timed_out else "exit %s" % res.rc))
    row = parse_windows(res.out)
    if row is None:
        return None, "%s answered but the probe line was not readable" % h["id"]
    return row, ""


def alias_result(ctx, alias):
    """The one no-op ssh probe of an alias this tick (shared by every check that reads it): the ssh Result."""
    return ctx.once(("ssh-alias", alias), lambda: ctx.run(_ssh_base(ctx) + [alias, "echo " + MARK], timeout=ctx.d["ssh_timeout_s"] + 8))


def guest_answers(ctx, h):
    """(answered, error text) for the guest (a WSL distribution or VM) behind a host, asked over its own ssh alias; shared per tick.
    `answered` is None when there is no guest, or when its login waits for an additional check (it answered, and is not hung, but nothing was learned)."""
    g = h.get("guest")
    if not g:
        return None, ""
    res = alias_result(ctx, g["ssh"])
    if sshclass.recognise((res.err or "") + (res.out or "")):
        return None, "waiting for an additional ssh check"
    hcs = [ln.strip() for ln in (res.err or "").splitlines() if "HCS_E_" in ln or "0x8037" in ln]   # WSL's own reason it cannot start, often not the first line
    return bool(res.ok and MARK in res.out), (hcs[0][:160] if hcs else _first_err(res.err)) or "timed out"


def probe(ctx, h):
    """One host: ('ok'|'down', findings, detail)."""
    alias, hid = h["ssh"], h["id"]
    osk = h.get("os", "linux")
    res = ssh_result(ctx, h)
    out = res.out
    if res.ok and (MARK in out or "INFIRMARY " in out):
        findings = []
        detail = "%s answers ssh" % hid
        if osk == "windows":
            row = parse_windows(out)
            if row is None:
                return "ok", [], "%s answers ssh but the memory probe returned nothing readable" % hid
            findings += _windows_findings(ctx, h, row)
            detail = "%s: %d MB free of %d MB, commit %s%%, %d processes" % (hid, row.get("avail_mb", 0), row.get("total_mb", 0), row.get("commit_pct"), row.get("procs", 0))
        else:
            findings += _guest(ctx, h)       # a Windows host's guest is the WSL check's (it adds what the Windows side sees)
        return "ok", findings, detail
    # ssh failed: say how far it got
    if sshclass.recognise((res.err or "") + (res.out or "")):    # the server answered and wants a person's sign-in first: not hung, and the ssh-check finding says so
        return "down", [], "%s: ssh waits for an additional check (see ssh-check)" % hid
    err = _first_err(res.err)
    if res.timed_out or res.rc == 255 or res.rc is None:
        host, port = _endpoint(ctx, alias)
        stage, text = _tcp_stage(host, port, 3)
        auth = bool(re.search(r"permission denied|host key verification|no matching", err, re.I))
        if stage == "banner" or auth:
            return "down", [finding.make("hung-host", 2, "host-unreachable", hid, "WARNING", hid, hid, "%s answers on port %d but ssh login failed" % (hid, port),
                                         "sshd greets (%s) but a non-interactive login failed: %s" % (text, err or "no error text"),
                                         evidence=[finding.fact("ssh", err or "login failed", alias), finding.fact("tcp", text)],
                                         recommendation="This is not a hung host: the login itself is refused or the key is missing. Check the ssh key and the host's account.",
                                         lk_kind="machine")], "%s: login failed" % hid
        if stage == "silent":
            return "down", [finding.make("hung-host", 2, "host-hung", hid, "CRITICAL", hid, hid, "%s accepts connections but does not answer ssh" % hid,
                                         "TCP port %d accepts, yet no ssh greeting arrives within 3 s and the login timed out: the host (or its WSL or VM) is stalled, usually out of memory" % port,
                                         evidence=[finding.fact("ssh", "no reply to a %ds-timeout login" % ctx.d["ssh_timeout_s"], alias), finding.fact("tcp", text)],
                                         how=finding.how("a person at the machine", "Recovery steps stop every process on that machine, so they are a person's call after every lane there is recorded. The Infirmary only sets the advisory hung and drain flags."),
                                         recommendation="Treat the host as hung: send no new work, record which lanes it holds, and restart it only after that.",
                                         lk_kind="machine")], "%s: hung" % hid
        return "down", [finding.make("hung-host", 2, "host-unreachable", hid, "WARNING", hid, hid, "%s does not answer" % hid,
                                     "ssh timed out and nothing accepts on port %d (%s); the machine may be off, asleep or cut off from the network" % (port, text),
                                     evidence=[finding.fact("ssh", err or "timed out", alias), finding.fact("tcp", text)],
                                     recommendation="Confirm whether the machine is meant to be up. Send no new work there until it answers.", lk_kind="machine")], "%s: unreachable" % hid
    return "down", [finding.make("hung-host", 2, "host-unreachable", hid, "UNKNOWN", hid, hid, "%s: the probe command failed" % hid,
                                 "ssh connected but the probe exited %s: %s" % (res.rc, err or "no error text"),
                                 evidence=[finding.fact("ssh", err or "exit %s" % res.rc, alias)], lk_kind="machine")], "%s: probe failed" % hid


def _windows_findings(ctx, h, row):
    w, hid = ctx.d["windows"], h["id"]
    avail, commit = row.get("avail_mb"), row.get("commit_pct")
    wsl = row.get("wslconfig") or []
    row["wslconfig"] = [x.get("value", "") if isinstance(x, dict) else str(x) for x in (wsl if isinstance(wsl, list) else [wsl])]
    why, status = [], None
    if isinstance(avail, (int, float)):
        if avail < w["avail_crit_mb"]:
            status = "CRITICAL"
            why.append("only %d MB of %d MB free" % (avail, row.get("total_mb", 0)))
        elif avail < w["avail_warn_mb"]:
            status = status or "WARNING"
            why.append("%d MB of %d MB free" % (avail, row.get("total_mb", 0)))
    if isinstance(commit, (int, float)):
        if commit >= w["commit_crit_pct"]:
            status = "CRITICAL"
            why.append("commit charge %.0f%% of its limit" % commit)
        elif commit >= w["commit_warn_pct"]:
            status = status or "WARNING"
            why.append("commit charge %.0f%% of its limit" % commit)
    old = row.get("old_bash", 0) + row.get("old_sshd", 0) + row.get("old_powershell", 0)
    ev = [finding.fact("windows probe", "%d MB free of %d MB, commit %s%%, %d processes, up %s h" % (avail or 0, row.get("total_mb", 0), commit, row.get("procs", 0), row.get("uptime_h"))),
          finding.fact("windows probe", "older than %dh: %d bash, %d sshd, %d powershell" % (w["old_process_hours"], row.get("old_bash", 0), row.get("old_sshd", 0), row.get("old_powershell", 0)))]
    if row.get("vmmem_n"):
        ev.append(finding.fact("windows probe", "the VM (vmmem) holds %d MB; WSL caps in .wslconfig: %s" % (row.get("vmmem_mb", 0), "; ".join(row.get("wslconfig") or []) or "none set (defaults: half of RAM, quarter swap)")))
    if old >= w["old_process_warn"]:
        why.append("%d farm processes older than %dh are still running" % (old, w["old_process_hours"]))
        status = status or "WARNING"
    if not status:
        return []
    return [finding.make("hung-host", 2, "host-memory-short", hid, status, hid, hid, "%s is short of memory" % hid, "; ".join(why), evidence=ev,
                         how=finding.how("a person at the machine", "Out-of-memory Windows hosts are where WSL and ssh hang first; the Infirmary sets the drain flag and stops nothing."),
                         recommendation="Send no new work to this host; find what holds the memory (old farm processes, the VM) before it hangs.", lk_kind="capacity")]


def _guest(ctx, h):
    g = h.get("guest")
    ans, err = guest_answers(ctx, h)
    if ans is None or ans:
        return []
    return [finding.make("hung-host", 2, "host-hung", "%s/%s" % (h["id"], g.get("id", "guest")), "CRITICAL", h["id"], "%s/%s" % (h["id"], g.get("id", "guest")),
                         "the guest on %s does not answer while the host does" % h["id"],
                         "%s answers ssh but its guest %s does not (%s)" % (h["id"], g["ssh"], err),
                         evidence=[finding.fact("ssh", "host answered, guest %s did not" % g["ssh"], g["ssh"])],
                         how=finding.how("a person at the machine", "Recovery stops every distribution and lane on that machine; it is a person's call after every lane there is recorded."),
                         recommendation="Treat the guest as hung; record its lanes before anyone restarts the VM.", lk_kind="machine")]


def check_hosts(ctx):
    hosts = ctx.d["hosts"]
    if not hosts:
        return registry.skip("no other hosts configured (doctor.hosts)")
    findings, parts, probed = [], [], []
    for h in hosts:
        _, fs, detail = probe(ctx, h)
        findings += fs
        parts.append(detail)
        probed.append(h["id"])
    return registry.found(findings, "; ".join(parts), probed=probed)


def check_sockets(ctx):
    pats = ctx.d["channels"]["control_sockets"]
    if not pats:
        return registry.skip("no ssh control sockets configured (doctor.channels.control_sockets)")
    socks = ctx.globs(pats)
    if not socks:
        return registry.ok("no control socket exists right now")
    findings = []
    for s in socks[:20]:
        res = ctx.run(["ssh", "-o", "BatchMode=yes", "-S", s, "-O", "check", "infirmary-probe"], timeout=5)
        if res.ok:
            continue
        findings.append(finding.make("control-sockets", 3, "control-socket-dead", s, "WARNING", ctx.host_id, os.path.basename(s),
                                     "an ssh control socket has no live master", "`ssh -O check` on %s failed: %s" % (os.path.basename(s), _first_err(res.err) or "timed out"),
                                     evidence=[finding.fact("ssh -O check", _first_err(res.err) or "timed out", s)],
                                     how=finding.how("ssh", "Closing the socket ends every multiplexed session on it, so that is a person's call; a fresh connection makes a new master."),
                                     recommendation="Runs that go through this socket will hang until it is replaced.", lk_kind="machine"))
    return registry.found(findings, "%d socket(s) checked" % len(socks[:20]))


SPECS = [registry.Spec("hung-host", 2, "Other hosts (hung or out of memory)", check_hosts, remote=True),
         registry.Spec("control-sockets", 3, "ssh control sockets", check_sockets)]
