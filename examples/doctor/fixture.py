#!/usr/bin/env python3
"""Build the generic Infirmary fixtures: synthetic /proc trees, recorded tool output behind fake shims, small file trees.

    python3 examples/doctor/fixture.py OUTDIR

Writes OUTDIR and OUTDIR/scenarios.json; `scripts/test` replays every scenario through `bin/infirmary` and compares
the findings with what each scenario expects. A scenario sets up a situation the real machine is not in (a host out of
memory, a hung ssh, a full disk, an orphaned server, a clobbered checkout) and says which findings the Infirmary must
report, and, as important, which look-alikes it must leave alone.
"""
import copy
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixlib  # noqa: E402
from fixlib import FakeProc, run, scenario, write  # noqa: E402

HOST = "fixture-host"


def common(root, proc=None, extra=(), hysteresis=False):
    """The settings every generic run shares: no real beat, a named host, a quiet disk, a synthetic /proc."""
    sets = ["host.id=" + HOST, "doctor.platform=linux", "doctor.hysteresis=" + ("true" if hysteresis else "false"), "beat.path=" + os.path.join(root, "no-beat.json"),
            "doctor.disk.paths=" + json.dumps([{"path": root, "label": "fixture", "warn_gb": 0, "crit_gb": 0, "warn_pct": 0, "crit_pct": 0}])]
    if proc:
        sets.append("doctor.proc_root=" + proc)
    sets += list(extra)
    out = ["check"]
    for s in sets:
        out += ["--set", s]
    return out


def memory_scenarios(root):
    out = []
    # a host with no memory left, and servers left behind by finished jobs
    p = FakeProc(os.path.join(root, "proc-sick"))
    p.memory(total_gb=16, avail_pct=3, swap_gb=8, swap_used_pct=95, psi_full=20)
    p.add(1, 0, ["/sbin/init"], 900000)
    p.add(900, 1, ["/usr/local/bin/fixture-server", "serve"], 7200, rss_mb=600)        # orphan: parent is init, old, big
    p.add(901, 1, ["/usr/local/bin/fixture-server", "serve"], 120, rss_mb=700)         # too young to call orphaned
    p.add(902, 1, ["/usr/local/bin/fixture-server", "serve"], 7200, rss_mb=20)         # too small to matter
    p.add(903, 950, ["/usr/local/bin/fixture-server", "serve"], 7200, rss_mb=600)      # has a live parent
    p.add(950, 1, ["/bin/bash", "job.sh"], 7300)
    p.add(904, 1, ["/usr/local/bin/other-server"], 7200, rss_mb=900)                   # a name nobody listed
    extra = ["doctor.memory.orphans.names=[\"fixture-server\"]"]
    out.append(scenario("memory-and-orphans", [run(common(root, p.root, extra), {
        "exit": 30, "findings": ["memory-pressure:" + HOST, "orphan-servers:" + HOST], "checks_state": {"memory-swap": "finding", "orphan-servers": "finding"},
        "flags": {HOST: {"drain": True, "hung": None}}, "stdout_lacks": ["identity pid"]}),
        run(common(root, p.root, extra), {"exit": 30, "text_has": ["CRIT", "1 orphaned server(s) hold 0.6 GB", "memory and swap are exhausted"]}, text=True)]))
    # a lot of swap in use but plenty of memory: cold pages, not an emergency
    q = FakeProc(os.path.join(root, "proc-cold-swap"))
    q.memory(total_gb=16, avail_pct=45, swap_gb=8, swap_used_pct=95, psi_full=0.1)
    q.add(1, 0, ["/sbin/init"], 900000)
    q.add(900, 1, ["/usr/local/bin/fixture-server", "serve"], 120, rss_mb=600)
    out.append(scenario("memory-swap-full-but-memory-free", [run(common(root, q.root, extra), {
        "exit": 0, "findings": [], "checks_state": {"memory-swap": "ok", "orphan-servers": "ok"}, "flags": {HOST: {"drain": False}}})]))
    # nothing readable: the answer is unknown, never fine
    os.makedirs(os.path.join(root, "proc-empty"), exist_ok=True)
    out.append(scenario("memory-unreadable-is-unknown", [run(common(root, os.path.join(root, "proc-empty")), {
        "exit": 50, "findings": [], "checks_state": {"memory-swap": "unknown"}, "flags": {HOST: {"drain": None}}})]))
    # the for-duration: first look only watches, a finding seen long enough is confirmed
    base = common(root, p.root, extra, hysteresis=True)
    out.append(scenario("memory-needs-its-for-duration", [
        run(base, {"exit": 10, "findings": ["orphan-servers:" + HOST], "pending": ["memory-pressure:" + HOST]}),
        run(base, {"exit": 30, "findings": ["memory-pressure:" + HOST, "orphan-servers:" + HOST], "pending": []}, seen={"memory-pressure:" + HOST: {"first_ago": 400, "last_ago": 0}}),
        run(base, {"exit": 10, "findings": ["orphan-servers:" + HOST], "pending": ["memory-pressure:" + HOST]}, seen={"memory-pressure:" + HOST: {"first_ago": 4400, "last_ago": 4000}})]))
    return out


def beat_scenarios(root):
    """The host beat (a `lookout.beat` file) is read when fresh and ignored when it has stopped."""
    p = FakeProc(os.path.join(root, "proc-beat"))        # the kernel's own numbers say all is well
    p.memory(total_gb=16, avail_pct=50, swap_gb=8, swap_used_pct=5, psi_full=0.1)
    beat = {"schema": "lookout.beat", "schema_version": "1.0", "host": HOST, "seq": 7, "interval_s": 15,
            "machine": {"id": HOST, "mem": {"swap_used_pct": 96.0}, "disks": [], "pressure": {"level": "critical", "why": ["swapping 410 pages/s", "run queue 3.2 per core"]}}}
    fresh, old = os.path.join(root, "beat-fresh.json"), os.path.join(root, "beat-old.json")
    for path in (fresh, old):
        write(path, json.dumps(beat))
    fixlib.touch_age(old, 3600)
    args = lambda b: common(root, p.root, ["beat.path=" + b])
    return [scenario("beat-critical-and-fresh-is-reported", [run(args(fresh), {"exit": 30, "findings": ["memory-pressure:" + HOST], "evidence_has": ["swapping 410 pages/s"],
                                                                               "flags": {HOST: {"drain": True}}})]),
            scenario("beat-that-stopped-is-not-evidence", [run(args(old), {"exit": 0, "findings": [], "checks_state": {"memory-swap": "ok"}})])]


def mac_scenarios(root):
    """macOS parsers against shims; the output formats are the documented ones, not captured on a Mac."""
    out = []
    page = 16384

    def mac_dir(name, free_pages, swap_used_m, level, ps_extra=()):
        d = os.path.join(root, name)
        write(os.path.join(d, "vm_stat.txt"), "Mach Virtual Memory Statistics: (page size of %d bytes)\nPages free:                  %d.\nPages active:           400000.\n"
              "Pages inactive:               %d.\nPages speculative:             300.\nPages throttled:                 0.\nPages wired down:           180000.\nPages purgeable:               200.\n" % (page, free_pages, free_pages * 2))
        write(os.path.join(d, "sysctl.hw.memsize"), "17179869184\n")
        write(os.path.join(d, "sysctl.vm.swapusage"), "vm.swapusage: total = 20480.00M  used = %.2fM  free = %.2fM  (encrypted)\n" % (swap_used_m, 20480 - swap_used_m))
        write(os.path.join(d, "sysctl.kern.memorystatus_vm_pressure_level"), "%d\n" % level)
        now = time.time()

        def lstart(age):
            return time.strftime("%a %b %e %H:%M:%S %Y", time.gmtime(now - age))
        rows = ["    1     0     0   9000 %s /sbin/launchd" % lstart(900000), "  800     1   501  20000 %s /bin/bash job.sh" % lstart(7300)]
        rows += list(ps_extra) and [r % lstart(7200) for r in ps_extra]
        write(os.path.join(d, "ps.txt"), "\n".join(rows) + "\n")
        return d
    sick = mac_dir("mac-sick", free_pages=900, swap_used_m=19900, level=4, ps_extra=["54001     1   %d 614400 %%s /usr/local/bin/fixture-server serve --data-dir /x" % os.getuid()])
    good = mac_dir("mac-good", free_pages=90000, swap_used_m=300, level=1, ps_extra=["54001     1   %d 614400 %%s /usr/local/bin/fixture-server serve" % os.getuid()])
    extra = ["doctor.memory.orphans.names=[\"fixture-server\"]"]
    shim_path = os.pathsep.join([os.path.join(HERE, "shims-macos"), os.environ.get("PATH", "")])
    for name, d, want in (("macos-memory-critical", sick, {"exit": 30, "findings": ["memory-pressure:" + HOST, "orphan-servers:" + HOST]}),
                          ("macos-memory-healthy", good, {"exit": 10, "findings": ["orphan-servers:" + HOST]})):
        args = common(root, None, extra + ["doctor.platform=macos"])
        out.append(scenario(name, [run(args, dict(want, checks_state={"memory-swap": "ok" if want["exit"] != 30 else "finding"}),
                                       env={"PATH": shim_path, "INFIRMARY_MAC_DIR": d})]))
    return out


def disk_scenarios(root):
    out = []
    scratch = os.path.join(root, "scratch")
    for i in range(3):
        write(os.path.join(scratch, "job%d" % i, "blob.bin"), "x" * (1 << 20))
    vol = lambda warn, crit: ["doctor.disk.paths=" + json.dumps([{"path": root, "label": "fixture", "warn_gb": warn, "crit_gb": crit, "warn_pct": 0, "crit_pct": 0}]),
                              "doctor.disk.inventory=" + json.dumps([{"label": "agent scratch", "path": scratch}])]
    p = FakeProc(os.path.join(root, "proc-disk"))
    out.append(scenario("disk-critical-with-inventory", [run(common(root, p.root, vol(1e9, 1e9 + 1)), {
        "exit": 30, "findings": ["disk-low:fixture"], "flags": {HOST: {"disk_critical": True, "drain": True}}, "evidence_has": ["agent scratch"]})]))
    out.append(scenario("disk-warning", [run(common(root, p.root, vol(1e9, 0)), {"exit": 10, "findings": ["disk-low:fixture"], "flags": {HOST: {"disk_critical": False}}})]))
    # growth: yesterday's sample says used space was far lower, so the volume fills in under three days
    st = os.statvfs(root)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    free = st.f_bavail * st.f_frsize
    hist = {"fixture": [[int(time.time()) - 86400, used - (1 << 30)]]}        # one GB a day
    out.append(scenario("disk-growth-projects-a-full-volume", [run(common(root, p.root, ["doctor.disk.growth_days=100000"]), {"exit": 10, "findings": ["disk-growth:fixture"]},
                                                                   setup=[{"write_state": "disk-history.json", "json": hist}])]))
    out.append(scenario("disk-quiet-is-ok", [run(common(root, p.root), {"exit": 0, "findings": [], "checks_state": {"disk-exhaustion": "ok"}, "flags": {HOST: {"disk_critical": False}}})]))
    return out


def host_scenarios(root):
    out = []
    p = FakeProc(os.path.join(root, "proc-hosts"))
    shim_path = os.pathsep.join([os.path.join(HERE, "shims"), os.environ.get("PATH", "")])
    hosts = [{"id": "win-ok", "ssh": "win-ok", "os": "windows"}, {"id": "win-low", "ssh": "win-low", "os": "windows"},
             {"id": "hung-host", "ssh": "hung-host", "os": "linux"}, {"id": "down-host", "ssh": "down-host", "os": "linux"},
             {"id": "auth-host", "ssh": "auth-host", "os": "linux"},
             {"id": "linux-ok", "ssh": "linux-ok", "os": "linux", "guest": {"ssh": "guest-dead", "id": "vm"}},
             {"id": "linux-fine", "ssh": "linux-ok", "os": "linux", "guest": {"ssh": "guest-ok", "id": "vm"}}]
    args = common(root, p.root, ["doctor.hosts=" + json.dumps(hosts), "doctor.ssh_timeout_s=2"]) + ["--only", "hung-host"]
    out.append(scenario("hosts-hung-down-and-out-of-memory", [run(args, {
        "exit": 30,
        "findings": ["host-memory-short:win-low", "host-hung:hung-host", "host-unreachable:down-host", "host-unreachable:auth-host", "host-hung:linux-ok/vm"],
        "flags": {"win-low": {"drain": True, "hung": False}, "hung-host": {"hung": True, "drain": True}, "win-ok": {"hung": False, "drain": None}, "linux-fine": {"hung": False}}},
        env={"PATH": shim_path})], servers=[{"kind": "silent", "env": "INFIRMARY_PORT_SILENT"}, {"kind": "banner", "env": "INFIRMARY_PORT_BANNER"},
                                            {"kind": "closed", "env": "INFIRMARY_PORT_CLOSED"}]))
    out.append(scenario("hosts-recorded-healthy-windows-probe", [run(
        common(root, p.root, ["doctor.hosts=" + json.dumps([hosts[0]])]) + ["--only", "hung-host"], {"exit": 0, "findings": [], "checks_state": {"hung-host": "ok"}}, env={"PATH": shim_path})]))
    out.append(scenario("hosts-none-configured-is-skipped", [run(common(root, p.root) + ["--only", "hung-host"], {"exit": 0, "findings": [], "checks_state": {"hung-host": "skipped"}})]))
    socks = os.path.join(root, "socks")
    for n in ("s-live", "s-dead"):
        write(os.path.join(socks, n), "")
    out.append(scenario("control-sockets", [run(common(root, p.root, ["doctor.channels.control_sockets=" + json.dumps([socks + "/s-*"])]) + ["--only", "control-sockets"], {
        "exit": 10, "findings": ["control-socket-dead:" + os.path.join(socks, "s-dead")]}, env={"PATH": shim_path})]))
    return out


def clobber_scenarios(root):
    def pkg(w, *names):
        for n in names:
            os.makedirs(os.path.join(w, "node_modules", n), exist_ok=True)
    base = os.path.join(root, "worktrees")
    pkg(os.path.join(base, "wt-clobbered"), "react", "@rolldown/binding-win32-x64-msvc", "@esbuild/win32-x64")
    pkg(os.path.join(base, "wt-both"), "react", "@rolldown/binding-win32-x64-msvc", "@rolldown/binding-linux-x64-gnu", "@esbuild/win32-x64", "@esbuild/linux-x64")
    pkg(os.path.join(base, "wt-linux"), "react", "@rolldown/binding-linux-x64-gnu")
    pkg(os.path.join(base, "wt-pnpm"), ".pnpm/@rolldown+binding-win32-x64-msvc@1.0.0", ".pnpm/react@18.0.0")
    os.makedirs(os.path.join(base, "wt-nodeps"), exist_ok=True)
    roots = [{"label": n, "path": os.path.join(base, n)} for n in ("wt-clobbered", "wt-both", "wt-linux", "wt-pnpm", "wt-nodeps")]
    p = FakeProc(os.path.join(root, "proc-clobber"))
    return [scenario("clobbered-native-bindings", [run(common(root, p.root, ["doctor.clobber.roots=" + json.dumps(roots)]) + ["--only", "clobbered-checkout"], {
        "exit": 10, "findings": ["clobbered-bindings:" + os.path.join(base, "wt-clobbered"), "clobbered-bindings:" + os.path.join(base, "wt-pnpm")], "checks_state": {"clobbered-checkout": "finding"}})])]


def interop_scenarios(root):
    """Catalogue 19: the WSL interop entry in the kernel's binfmt_misc table."""
    out = []

    def guest(name, entries=None, mounted=True):
        p = FakeProc(os.path.join(root, name))
        p.add(1, 0, ["/sbin/init"], 900000)
        table = os.path.join(p.root, "sys", "fs", "binfmt_misc")
        os.makedirs(table, exist_ok=True)
        if mounted:
            write(os.path.join(table, "register"), "")
            write(os.path.join(table, "status"), "enabled\n")
        for n, body in (entries or {}).items():
            write(os.path.join(table, n), body)
        return p
    args = lambda p, plat="wsl": common(root, p.root, ["doctor.platform=" + plat]) + ["--only", "wsl-interop"]      # noqa: E731
    out.append(scenario("interop-registered-is-quiet", [run(args(guest("proc-interop-ok", {"WSLInterop": "enabled\ninterpreter /init\nflags: P\n", "python3.14": "enabled\n"})), {
        "exit": 0, "findings": [], "checks_state": {"wsl-interop": "ok"}, "json_has": {"local.wsl-interop.registered": "WSLInterop"}})]))
    out.append(scenario("interop-late-registration-counts", [run(args(guest("proc-interop-late", {"WSLInterop-late": "enabled\n"})), {
        "exit": 0, "findings": [], "checks_state": {"wsl-interop": "ok"}})]))
    out.append(scenario("interop-missing-after-a-restart", [run(args(guest("proc-interop-missing", {"python3.14": "enabled\n"})), {
        "exit": 10, "findings": ["wsl-interop-missing:" + HOST], "evidence_has": ["entries: python3.14"], "how_has": ["needs root"],
        "json_has": {"local.wsl-interop.registered": None}})]))
    out.append(scenario("interop-registered-but-disabled", [run(args(guest("proc-interop-off", {"WSLInterop": "disabled\n"})), {
        "exit": 10, "findings": ["wsl-interop-missing:" + HOST], "evidence_has": ["WSLInterop is disabled"]})]))
    out.append(scenario("interop-table-not-mounted-is-unknown", [run(args(guest("proc-interop-unmounted", mounted=False)), {
        "exit": 50, "findings": [], "checks_state": {"wsl-interop": "unknown"}})]))
    out.append(scenario("interop-is-skipped-off-wsl", [run(args(guest("proc-interop-linux"), "linux"), {
        "exit": 0, "findings": [], "checks_state": {"wsl-interop": "skipped"}})]))
    return out


def scratch_scenarios(root):
    """Catalogue 20: scratch a job left behind (with the look-alikes that are in use, too young or too small) and a RAM-backed volume that filled up."""
    out = []

    def entry(tmp, name, mb, age):
        d = os.path.join(tmp, name)
        write(os.path.join(d, "data.bin"), "x" * (mb << 20))
        fixlib.touch_age(d, age)
        return d
    tmp = os.path.join(root, "scratch-tmp")
    for n in ("old1", "old2", "old3"):
        entry(tmp, "browser-profile-" + n, 3, 7200)             # leaked
    entry(tmp, "browser-profile-live", 3, 7200)                 # a running browser's command line names it
    entry(tmp, "browser-profile-cwd", 3, 7200)                  # a process works in it
    entry(tmp, "browser-profile-open", 3, 7200)                 # a process holds a file in it open
    entry(tmp, "browser-profile-young", 3, 60)                  # younger than the minimum age
    entry(tmp, "browser-profile-prefix", 3, 7200)               # a browser names browser-profile-prefix-longer: another path, so this one is leaked
    entry(tmp, "floor-data-100-4242-0", 3, 7200)                # its owner (pid 4242, in the name) is alive
    entry(tmp, "floor-data-100-4243-0", 3, 7200)                # its owner is gone
    os.makedirs(os.path.join(root, "elsewhere"), exist_ok=True)
    os.symlink(os.path.join(root, "elsewhere"), os.path.join(tmp, "browser-profile-link"))      # a link is never followed or counted
    def procs(name):
        p = FakeProc(os.path.join(root, name))
        p.add(1, 0, ["/sbin/init"], 900000)
        p.add(600, 1, ["/opt/browser/chrome", "--headless", "--user-data-dir=" + os.path.join(tmp, "browser-profile-live")], 7000)
        p.add(601, 1, ["/bin/bash"], 7000, cwd=os.path.join(tmp, "browser-profile-cwd"))
        p.add(602, 1, ["/usr/bin/node", "job.js"], 7000, fds=[os.path.join(tmp, "browser-profile-open", "data.bin")])
        p.add(603, 1, ["/opt/browser/chrome", "--user-data-dir=" + os.path.join(tmp, "browser-profile-prefix-longer")], 7000)
        p.add(4242, 1, ["/usr/bin/node", "scripts/test-floor.mjs"], 7000)
        return p
    p = procs("proc-scratch")
    groups = [{"label": "browser profiles", "glob": os.path.join(tmp, "browser-profile-*"), "min_age_s": 1800, "min_mb": 1},
              {"label": "floor data", "glob": os.path.join(tmp, "floor-data-*"), "min_age_s": 1800, "min_mb": 1, "owner_pid_re": "-([0-9]+)-[0-9]+$"}]
    args = lambda extra=(), proc=p: common(root, proc.root, ["doctor.scratch.groups=" + json.dumps(groups)] + list(extra)) + ["--only", "scratch-leaks"]      # noqa: E731
    out.append(scenario("scratch-leaked-entries-are-found-and-the-ones-in-use-are-left", [run(args(), {
        "exit": 10, "findings": ["scratch-leaked:browser profiles", "scratch-leaked:floor data"], "checks_state": {"scratch-leaks": "finding"},
        "evidence_has": ["4 of 8 entries matching", "12 MB in all", "no live process names any of them: checked", "left alone, in use: browser-profile-live",
                         "left alone, in use: browser-profile-cwd", "left alone, in use: browser-profile-open", "1 of 2 entries matching"],
        "unchanged": [tmp], "json_has": {"local.scratch-leaks.groups.browser profiles.entries": 8, "local.scratch-leaks.groups.browser profiles.leaked": 4,
                                         "local.scratch-leaks.groups.browser profiles.in_use": 3, "local.scratch-leaks.groups.browser profiles.young": 1,
                                         "local.scratch-leaks.groups.floor data.leaked": 1, "local.scratch-leaks.groups.floor data.in_use": 1}})]))
    # the look-alikes alone: everything is in use or young; a group below its size threshold; no group configured at all
    ok = os.path.join(root, "scratch-ok")
    entry(ok, "browser-profile-young", 3, 60)
    entry(ok, "browser-profile-live", 3, 7200)
    q = FakeProc(os.path.join(root, "proc-scratch-ok"))
    q.add(1, 0, ["/sbin/init"], 900000)
    q.add(600, 1, ["/opt/browser/chrome", "--user-data-dir=" + os.path.join(ok, "browser-profile-live")], 7000)
    ok_groups = [{"label": "browser profiles", "glob": os.path.join(ok, "browser-profile-*"), "min_age_s": 1800, "min_mb": 1}]
    out.append(scenario("scratch-in-use-or-young-is-quiet", [run(common(root, q.root, ["doctor.scratch.groups=" + json.dumps(ok_groups)]) + ["--only", "scratch-leaks"], {
        "exit": 0, "findings": [], "checks_state": {"scratch-leaks": "ok"}, "unchanged": [ok], "json_has": {"local.scratch-leaks.groups.browser profiles.leaked": 0, "local.scratch-leaks.groups.browser profiles.in_use": 1}})]))
    small = os.path.join(root, "scratch-small")
    entry(small, "browser-profile-old", 1, 7200)
    out.append(scenario("scratch-leaked-but-small-is-quiet", [run(common(root, q.root, ["doctor.scratch.groups=" + json.dumps([dict(ok_groups[0], glob=os.path.join(small, "browser-profile-*"), min_mb=50)])]) + ["--only", "scratch-leaks"], {
        "exit": 0, "findings": [], "checks_state": {"scratch-leaks": "ok"}, "json_has": {"local.scratch-leaks.groups.browser profiles.leaked": 1}})]))
    out.append(scenario("scratch-no-group-and-no-mount-table-is-skipped", [run(common(root, q.root) + ["--only", "scratch-leaks"], {
        "exit": 0, "findings": [], "checks_state": {"scratch-leaks": "skipped"}})]))
    # a RAM-backed volume: the mount table names the fixture directory as a tmpfs; the thresholds stand in for how full it is
    m = procs("proc-tmpfs")
    write(os.path.join(m.root, "mounts"), "proc /proc proc rw 0 0\ntmpfs %s tmpfs rw,nosuid,nodev 0 0\ntmpfs /run/lock tmpfs rw 0 0\n" % tmp)
    th = lambda warn, crit: ["doctor.scratch.tmpfs_warn_pct=%s" % warn, "doctor.scratch.tmpfs_crit_pct=%s" % crit, "doctor.scratch.tmpfs_min_mb=0"]      # noqa: E731
    out.append(scenario("tmpfs-over-its-warning-share-is-reported", [run(args(th(0, 101), m), {
        "exit": 10, "findings": ["tmpfs-full:" + tmp, "scratch-leaked:browser profiles", "scratch-leaked:floor data"], "flags": {HOST: {"drain": None}},
        "evidence_has": ["is a RAM-backed volume of", "memory and swap, not disk", "largest entries", "4 leaked browser profiles here hold", "1 leaked floor data here hold", "(see scratch-leaked)"], "unchanged": [tmp]})]))
    out.append(scenario("tmpfs-over-its-critical-share-drains-the-host", [run(args(th(0, 0), m), {
        "exit": 30, "findings": ["tmpfs-full:" + tmp, "scratch-leaked:browser profiles", "scratch-leaked:floor data"], "flags": {HOST: {"drain": True}}})]))
    out.append(scenario("tmpfs-with-room-is-quiet", [run(args(th(101, 102), m), {
        "exit": 10, "findings": ["scratch-leaked:browser profiles", "scratch-leaked:floor data"], "checks_state": {"scratch-leaks": "finding"}, "flags": {HOST: {"drain": None}}}),
        run(common(root, m.root, th(101, 102)) + ["--only", "scratch-leaks"], {"exit": 0, "findings": [], "checks_state": {"scratch-leaks": "ok"}})]))
    return out


def ssh_check_scenarios(root):
    """Catalogue 23: an ssh login that stops at an additional-check prompt (Tailscale SSH), with the look-alikes that must stay quiet.

    The fake `ssh` prints the prompt the way the real server does (a banner on standard error with a login URL on a second line), either
    closing at once (`check-closed`) or holding the connection until the Infirmary's own timeout kills it (`check-hold`). Every attempt is refused
    by the shim unless it is BatchMode with a connect timeout, no control master and the no-op `echo`: a probe that could prompt or run anything
    else would fail its scenario."""
    out = []
    p = FakeProc(os.path.join(root, "proc-sshcheck"))
    env = {"PATH": os.pathsep.join([os.path.join(HERE, "shims"), os.environ.get("PATH", "")])}
    url = "https://login.example.invalid/a/0123456789ab"

    def args(aliases=(), hosts=(), extra=(), only="ssh-check"):
        return common(root, p.root, ["doctor.ssh_check.aliases=" + json.dumps(list(aliases)), "doctor.hosts=" + json.dumps(list(hosts)), "doctor.ssh_timeout_s=2"] + list(extra)) + ["--only", only]
    tried = lambda **kw: {"local.ssh-check.aliases.%s.state" % a: st for a, st in kw.items()}      # noqa: E731
    mixed = ["check-closed", "linux-ok", "look-alike", "auth-host", "down-host"]
    out.append(scenario("ssh-check-prompt-is-found-with-its-url-and-the-other-answers-are-sorted", [run(args(mixed), {
        "exit": 10, "findings": ["ssh-check-required:check-closed"], "checks_state": {"ssh-check": "finding"},
        "evidence_has": ["printed: Tailscale SSH requires an additional check", "login URL printed: " + url, "did not open the URL and did not complete the check"],
        "how_has": ["checkPeriod", "never opens it"],
        "json_has": dict(tried(**{"check-closed": "check", "linux-ok": "ok", "look-alike": "ok", "auth-host": "auth", "down-host": "unreachable"}),
                         **{"local.ssh-check.aliases.check-closed.url_seen": True, "local.ssh-check.aliases.linux-ok.url_seen": False})}, env=env),
        run(args(mixed), {"exit": 10, "text_has": ["WARN", "ssh to check-closed needs an additional check", url, "steer", "push", "5 alias(es) tried: 2 ok, 1 waiting for an additional check, 1 login refused, 1 no answer"]},
            env=env, text=True)]))
    out.append(scenario("ssh-check-a-connection-held-open-is-ended-by-the-timeout-and-still-recognised", [run(args(["check-hold"], extra=["doctor.ssh_timeout_s=1"]), {
        "exit": 10, "findings": ["ssh-check-required:check-hold"], "evidence_has": ["login URL printed: " + url]}, env=env)]))
    out.append(scenario("ssh-check-a-prompt-without-a-url-is-still-reported", [run(args(["check-nourl"]), {
        "exit": 10, "findings": ["ssh-check-required:check-nourl"], "evidence_has": ["login URL printed: none"], "json_has": {"local.ssh-check.aliases.check-nourl.url_seen": False}}, env=env)]))
    out.append(scenario("ssh-check-banners-that-only-mention-a-check-are-quiet", [run(args(["look-alike", "linux-ok"]), {
        "exit": 0, "findings": [], "checks_state": {"ssh-check": "ok"}, "json_has": tried(**{"look-alike": "ok", "linux-ok": "ok"})}, env=env)]))
    # the aliases of doctor.hosts and their guests are tried through the probes hung-host already makes: one connection each, no hung-host finding for a host that is only waiting
    log = os.path.join(root, "ssh-attempts.log")
    hosts = [{"id": "box", "ssh": "check-closed", "os": "linux"}, {"id": "vm-host", "ssh": "linux-ok", "os": "linux", "guest": {"ssh": "check-guest", "id": "vm"}},
             {"id": "win-box", "ssh": "win-ok", "os": "windows"}]
    out.append(scenario("ssh-check-hosts-and-guests-share-one-connection-and-are-not-called-hung", [run(args(["check-closed"], hosts, only="hung-host,ssh-check"), {
        "exit": 10, "findings": ["ssh-check-required:check-closed", "ssh-check-required:check-guest"], "checks_state": {"ssh-check": "finding", "hung-host": "ok"},
        "flags": {"box": {"hung": False}, "vm-host": {"hung": False}}, "ssh_attempts": {"path": log, "counts": {"check-closed": 1, "check-guest": 1, "linux-ok": 1, "win-ok": 1}},
        "json_has": tried(**{"win-ok": "ok", "linux-ok": "ok", "check-guest": "check"})}, env=dict(env, INFIRMARY_SSH_LOG=log))]))
    # the operator's ssh config: plain Host names, wildcards and negations skipped, Include followed, excluded names never tried
    cfg = os.path.join(root, "ssh-home", "config")
    write(cfg, "# a comment\nHost *\n  ServerAliveInterval 30\nHost linux-ok check-closed\n  User someone\n  HostName 192.0.2.10\n  HostKeyAlias other\nhost=down-host\nHost !nope *.wild git-forge\nInclude conf.d/*.conf\n")
    write(os.path.join(root, "ssh-home", "conf.d", "more.conf"), "Host look-alike # trailing comment\n  Port 22\n")
    sc = ["doctor.ssh_check.from_ssh_config=true", "doctor.ssh_check.ssh_config=" + cfg, "doctor.ssh_check.exclude=" + json.dumps(["git-forge"])]
    out.append(scenario("ssh-check-reads-plain-host-names-from-the-ssh-config", [run(args(extra=sc), {
        "exit": 10, "findings": ["ssh-check-required:check-closed"], "unchanged": [os.path.dirname(cfg)],
        "json_has": dict(tried(**{"linux-ok": "ok", "look-alike": "ok", "check-closed": "check"}), **{"local.ssh-check.aliases.git-forge": None, "local.ssh-check.aliases.Name": None, "local.ssh-check.aliases.192": None, "local.ssh-check.aliases.down-host.state": "unreachable"})}, env=env)]))
    out.append(scenario("ssh-check-more-aliases-than-the-cap-are-named-not-tried", [run(args(["linux-ok", "look-alike", "check-closed", "auth-host", "down-host"], extra=["doctor.ssh_check.max_aliases=2"]), {
        "exit": 0, "findings": [], "checks_state": {"ssh-check": "ok"}, "text_has": [], "json_has": {"local.ssh-check.not_tried": 3, "local.ssh-check.aliases.check-closed": None, "local.ssh-check.aliases.look-alike.state": "ok"}}, env=env)]))
    out.append(scenario("ssh-check-nothing-to-try-is-skipped", [run(args(), {"exit": 0, "findings": [], "checks_state": {"ssh-check": "skipped"}}, env=env)]))
    out.append(scenario("ssh-check-no-alias-answering-is-unknown-not-fine", [run(args(["down-host", "hung-host"]), {
        "exit": 50, "findings": [], "checks_state": {"ssh-check": "unknown"}}, env=env)]))
    out.append(scenario("ssh-check-an-alias-that-could-be-an-ssh-option-is-refused", [run(args(["-oProxyCommand=true"]), {"exit": 2}, env=env, text=True)]))
    cap = os.path.join(root, "captured-feed-sshcheck.json")
    lk = ["lookout.enabled=true", "lookout.transport=local", "lookout.target=" + os.path.join(HERE, "shims", "lookout-capture")]
    live_on = run(["live", "on", "--by", "fixture", "--set", "host.id=" + HOST], {"exit": 0, "text_has": ["live repair: ON"]}, text=True)
    out.append(scenario("ssh-check-is-a-card-for-the-operator-and-live-repair-acts-on-nothing", [live_on, run(args(["check-closed"], extra=lk) + ["--push"], {
        "exit": 10, "findings": ["ssh-check-required:check-closed"], "repairs": [], "json_has": {"repair_mode": "live-repair"}, "log_lacks": ["repair", "refusal", "plan"],
        "feed": {"path": cap, "source": "infirmary", "cards": ["ssh to check-closed needs an additional check"], "card_severity": "action", "collector_ok": ["ssh-check"]}},
        env=dict(env, INFIRMARY_CAPTURE=cap))]))
    out.append(scenario("ssh-check-no-write-leaves-nothing-behind", [run(args(["check-closed"]) + ["--no-write"], {"exit": 10, "state_empty": True}, env=env)]))
    return out


def doc_scenarios(root):
    """doctor.json, the Lookout feed, --no-write, the kill switch: the shape other tools read."""
    p = FakeProc(os.path.join(root, "proc-doc"))
    p.memory(total_gb=16, avail_pct=3, swap_gb=8, swap_used_pct=95, psi_full=20)
    p.add(1, 0, ["/sbin/init"], 900000)
    cap = os.path.join(root, "captured-feed.json")
    shim = os.path.join(HERE, "shims", "lookout-capture")
    out = []
    push = ["lookout.enabled=true", "lookout.transport=local", "lookout.target=" + shim]
    out.append(scenario("doctor-json-and-lookout-feed", [run(common(root, p.root, push) + ["--push"], {
        "exit": 30, "findings": ["memory-pressure:" + HOST], "doctor_json": {"status": "CRITICAL", "flags_host": HOST, "schema": "infirmary.doctor"},
        "feed": {"path": cap, "source": "infirmary", "cards": ["memory and swap are exhausted"], "card_severity": "critical", "collector_ok": ["memory-swap", "disk-exhaustion"],
                 "machine_health": {HOST: "draining"}}}, env={"INFIRMARY_CAPTURE": cap})]))
    out.append(scenario("no-write-leaves-nothing-behind", [run(common(root, p.root) + ["--no-write"], {"exit": 30, "state_empty": True})]))
    live_on = run(["live", "on", "--by", "fixture", "--set", "host.id=" + HOST], {"exit": 0, "text_has": ["live repair: ON"]}, text=True)
    # even with live repair on, --no-write acts on nothing and writes nothing but the operator's own switch
    out.append(scenario("no-write-leaves-nothing-behind-even-with-live-repair-on", [
        live_on, run(common(root, p.root) + ["--no-write"], {"exit": 30, "json_has": {"repair_mode": "advice-only"}, "state_only": ["live-repair.json", "actions.jsonl"]})]))
    # live repair on with no adapter: the core has no actions of its own, so a sick host is reported and nothing is run or planned
    out.append(scenario("live-repair-without-actions-acts-on-nothing", [live_on, run(common(root, p.root), {
        "exit": 30, "findings": ["memory-pressure:" + HOST], "repairs": [], "json_has": {"repair_mode": "live-repair"}, "log_lacks": ["repair", "refusal", "plan"]})]))
    out.append(scenario("kill-switch", [
        run(["off"], {"exit": 0}),
        run(common(root, p.root), {"exit": 0, "text_has": ["infirmary is off"], "findings_none": True}, text=True),
        run(["on"], {"exit": 0}),
        run(common(root, p.root), {"exit": 30})]))
    return out


def windows_scenarios(root):
    """Catalogue 2 (WSL seen from Windows), 14 (capabilities) and 16 (leaked farm processes): the version-2 Windows probe.

    `win2-ok` is the recorded answer of a real Windows 11 host. The other `win2-*` answers are DERIVED from it by editing numbers
    and rows (a host nearly out of memory, a VM that is down, leaked servers); the derivation is in this function, so nothing
    invented sits among the recordings."""
    base = json.loads(open(os.path.join(HERE, "recorded", "windows-probe.v2.real.txt")).read()[len("INFIRMARY "):])
    probes = os.path.join(root, "probes")

    def derive(name, edit, stderr=None):
        d = copy.deepcopy(base)
        edit(d)
        write(os.path.join(probes, name + ".txt"), "INFIRMARY " + json.dumps(d, separators=(",", ":")) + "\n")
        if stderr:
            write(os.path.join(probes, name + ".stderr"), stderr)

    def vm(d, mb=None, cpu=None, avail=None, commit=None, up=True):
        d["vmmem_n"], d["vmmem_mb"], d["wsl_service"] = (1, mb if mb is not None else d["vmmem_mb"], "Running") if up else (0, 0, "Stopped")
        d["vmmem_cpu_pct"] = (cpu if cpu is not None else d["vmmem_cpu_pct"]) if up else None
        d["avail_mb"] = avail if avail is not None else d["avail_mb"]
        d["commit_pct"] = commit if commit is not None else d["commit_pct"]

    derive("win2-hot", lambda d: vm(d, mb=24100, cpu=97.5, avail=700, commit=93))           # at its cap, spinning, the host out of memory
    derive("win2-roomy", lambda d: vm(d, mb=24100, cpu=97.5, avail=20000, commit=40))        # at its cap and busy, but the host has room
    derive("win2-novm", lambda d: vm(d, up=False))
    row = lambda pid, name, age, ws, marks=(), tree=(0, 0), parent="dead", flags=(): {      # noqa: E731
        "pid": pid, "name": name, "born": "2026-10-09T08:00:00", "age_min": age, "ws_mb": ws, "parent": parent, "ppid": 900 + pid, "flags": list(flags), "marks": list(marks),
        "cmd_sha": "%010x" % (pid * 7919), "tree_n": tree[0], "tree_mb": tree[1]}
    real_chrome = base["leaks"]["rows"][0]

    def leaks(rows, sessions=()):
        def edit(d):
            d["leaks"] = {"scanned": 333, "total": len(rows), "rows": rows, "sessions": [{"name": n, "n": c, "mb": c * 4} for n, c in sessions]}
        return edit
    derive("win2-leaks", leaks([row(5101, "game-server-standalone.exe", 95, 1200), row(5102, "game-server-standalone.exe", 130, 1400),
                                row(5103, "chrome.exe", 200, 300, marks=["--headless"], tree=(12, 1800), flags=["--headless", "--remote-debugging-port"]),
                                real_chrome, row(5104, "node.exe", 400, 90)]))              # two servers, a headless browser tree; the person's chrome and a plain node are left alone
    derive("win2-leaks-small", leaks([row(5201, "game-server-standalone.exe", 45, 800, parent="recycled")]))
    derive("win2-leaks-own-only", leaks([real_chrome, row(5301, "node.exe", 500, 120), row(5302, "chrome.exe", 300, 200)]))   # parentless, none marked as farm work
    derive("win2-leaks-marked-node", leaks([row(5401, "node.exe", 300, 150, marks=["puppeteer"])]))
    derive("win2-leaks-unlisted", leaks([row(5501, "notepad.exe", 900, 40, marks=["--headless"])]))                      # a name nobody listed, even if marked
    derive("win2-sessions-many", leaks([], sessions=[("bash.exe", 30), ("sshd.exe", 45)]))
    derive("win2-sessions-few", leaks([], sessions=[("bash.exe", 5), ("sshd.exe", 4)]))
    derive("win2-profile", lambda d: None, stderr=". : File C:\\Users\\someone\\Documents\\WindowsPowerShell\\Microsoft.PowerShell_profile.ps1\ncannot be loaded because running scripts is disabled on this system.\n")

    def host(alias, hid=None, **kw):
        return dict({"id": hid or alias, "ssh": alias, "os": "windows"}, **kw)
    env = {"PATH": os.pathsep.join([os.path.join(HERE, "shims"), os.environ.get("PATH", "")]), "INFIRMARY_PROBES": probes}
    p = FakeProc(os.path.join(root, "proc-windows"))
    sel = ["--only", "wsl-vm,windows-capabilities,windows-leaks"]

    def args(hosts, extra=()):
        return common(root, p.root, ["doctor.hosts=" + json.dumps(hosts), "doctor.ssh_timeout_s=2"] + list(extra)) + sel
    qs = lambda *ids: {i: "ok" for i in ids}      # noqa: E731
    out = []

    # the recorded real host: quiet, and the document says what was seen
    out.append(scenario("windows-d3-recorded-real-host-is-quiet", [run(args([host("win2-ok")]), {
        "exit": 0, "findings": [], "checks_state": qs("wsl-vm", "windows-capabilities", "windows-leaks"),
        "flags": {"win2-ok": {"capability_drift": None}},
        "json_has": {"windows.win2-ok.wsl.vm_running": True, "windows.win2-ok.wsl.cap_mb": 24576, "windows.win2-ok.leaks.reported": 0, "windows.win2-ok.leaks.left_alone": 1,
                     "windows.win2-ok.capabilities.login_shell": "powershell", "windows.win2-ok.capabilities.login_profile_ok": True}}, env=env)]))
    # the guest does not answer: the VM is up (hung), the VM is down (stopped, not hung), the guest answers (nothing)
    out.append(scenario("windows-d3-wsl-guest-silent-with-vm-up-is-hung", [run(args([host("win2-hot", guest={"ssh": "guest-dead", "id": "wsl"})]), {
        "exit": 30, "findings": ["host-hung:win2-hot/wsl"], "evidence_has": ["is running and holds 24100 MB", "98% of its 24576 MB cap"],
        "flags": {"win2-hot": {"hung": True, "drain": True}}}, env=env)]))
    out.append(scenario("windows-d3-wsl-guest-silent-with-vm-down-is-stopped-not-hung", [run(args([host("win2-novm", guest={"ssh": "guest-dead", "id": "wsl"})]), {
        "exit": 10, "findings": ["wsl-not-running:win2-novm/wsl"], "flags": {"win2-novm": {"hung": None, "drain": None}}}, env=env)]))
    out.append(scenario("windows-d3-wsl-guest-answers-is-fine", [run(args([host("win2-ok", guest={"ssh": "guest-ok", "id": "wsl"})]), {
        "exit": 0, "findings": [], "checks_state": qs("wsl-vm")}, env=env)]))
    out.append(scenario("windows-d3-wsl-no-guest-and-vm-down-says-so-and-finds-nothing", [run(args([host("win2-novm")]), {
        "exit": 0, "findings": [], "checks_state": qs("wsl-vm"), "json_has": {"windows.win2-novm.wsl.vm_running": False}}, env=env)]))
    # near the cap and spinning: only with a host short of memory; with room it is a warning about the cap and no spin finding
    out.append(scenario("windows-d3-wsl-at-cap-on-a-starved-host-is-critical", [run(args([host("win2-hot")]), {
        "exit": 30, "findings": ["wsl-vm-near-cap:win2-hot", "wsl-vm-spinning:win2-hot"], "flags": {"win2-hot": {"drain": True, "hung": None}}, "evidence_has": ["98% of its 24576 MB cap"]}, env=env)]))
    out.append(scenario("windows-d3-wsl-at-cap-on-a-roomy-host-only-warns", [run(args([host("win2-roomy")]), {
        "exit": 10, "findings": ["wsl-vm-near-cap:win2-roomy"], "flags": {"win2-roomy": {"drain": None}}}, env=env)]))
    # leaked farm processes
    out.append(scenario("windows-d3-leaked-servers-and-headless-browser-tree", [run(args([host("win2-leaks")]), {
        "exit": 30, "findings": ["leaked-farm-processes:win2-leaks"], "evidence_has": ["pid 5101 game-server-standalone.exe", "pid 5103 chrome.exe", "marked by --headless", "(+12 child process(es), 1800 MB)", "parent gone"],
        "json_has": {"windows.win2-leaks.leaks.reported": 3, "windows.win2-leaks.leaks.left_alone": 2, "windows.win2-leaks.leaks.pids": [5101, 5102, 5103]},
        "stdout_lacks": ["pid 4232", "pid 5104"]}, env=env, text=False)]))
    out.append(scenario("windows-d3-one-small-leak-is-a-warning-and-a-reused-parent-counts", [run(args([host("win2-leaks-small")]), {
        "exit": 10, "findings": ["leaked-farm-processes:win2-leaks-small"], "evidence_has": ["pid reused by a younger process"]}, env=env)]))
    out.append(scenario("windows-d3-parentless-but-not-farm-work-is-left-alone", [run(args([host("win2-leaks-own-only")]), {
        "exit": 0, "findings": [], "checks_state": qs("windows-leaks"), "json_has": {"windows.win2-leaks-own-only.leaks.reported": 0, "windows.win2-leaks-own-only.leaks.left_alone": 3}}, env=env)]))
    out.append(scenario("windows-d3-node-is-a-leak-only-when-marked-as-farm-work", [
        run(args([host("win2-leaks-marked-node")]), {"exit": 10, "findings": ["leaked-farm-processes:win2-leaks-marked-node"]}, env=env),
        run(args([host("win2-leaks-marked-node")], ["doctor.windows.leaks.farm=" + json.dumps(["chrome.exe"])]), {"exit": 0, "findings": []}, env=env)]))
    out.append(scenario("windows-d3-a-name-nobody-listed-is-never-reported-even-if-marked", [run(args([host("win2-leaks-unlisted")]), {
        "exit": 0, "findings": [], "json_has": {"windows.win2-leaks-unlisted.leaks.reported": 0}}, env=env)]))
    out.append(scenario("windows-d3-many-orphaned-sessions-warn-a-few-do-not", [
        run(args([host("win2-sessions-many")]), {"exit": 10, "findings": ["leaked-farm-processes:win2-sessions-many"], "evidence_has": ["75 session process(es)"]}, env=env),
        run(args([host("win2-sessions-few")]), {"exit": 0, "findings": []}, env=env)]))
    # capabilities
    good = {"shell": "powershell", "gpu": ["RTX"], "tools": {"node": ">=20", "git": True}, "login_profile": "clean", "desktop": True}
    bad = {"shell": "cmd", "gpu": ["Radeon"], "gpu_preference": {"chrome.exe": "high"}, "tools": {"node": ">=99", "python": True, "rustc": True}}
    out.append(scenario("windows-d3-capabilities-as-declared-and-drift", [
        run(args([host("win2-ok", expect=good)]), {"exit": 0, "findings": [], "flags": {"win2-ok": {"capability_drift": False}}, "checks_state": qs("windows-capabilities")}, env=env),
        run(args([host("win2-ok", expect=bad)]), {
            "exit": 10, "findings": ["capability-drift:win2-ok"], "flags": {"win2-ok": {"capability_drift": True}},
            "evidence_has": ["login shell: declared cmd", "graphics adapter: declared Radeon", "graphics preference for chrome.exe", "tool python: declared present", "tool rustc", "tool node: declared >= 99"]}, env=env)]))
    out.append(scenario("windows-d3-a-login-profile-that-fails-is-drift-only-when-declared", [
        run(args([host("win2-profile")]), {"exit": 0, "findings": [], "json_has": {"windows.win2-profile.capabilities.login_profile_ok": False}}, env=env),
        run(args([host("win2-profile", expect={"login_profile": "clean"})]), {"exit": 10, "findings": ["capability-drift:win2-profile"]}, env=env)]))
    # older probe answer (no version 2 fields): what it cannot tell is unknown, never fine; the memory side still works
    out.append(scenario("windows-d3-an-older-probe-answer-is-unknown-not-fine", [run(args([host("win-low")]), {
        "exit": 50, "findings": [], "checks_state": {"wsl-vm": "ok", "windows-capabilities": "unknown", "windows-leaks": "unknown"}}, env=env)]))
    # a host that does not answer: the three checks say unknown and hung-host owns the finding
    servers = [{"kind": "closed", "env": "INFIRMARY_PORT_CLOSED"}]
    out.append(scenario("windows-d3-an-unreachable-host-is-unknown-here-and-found-by-hung-host", [run(
        common(root, p.root, ["doctor.hosts=" + json.dumps([host("down-host")]), "doctor.ssh_timeout_s=2"]) + ["--only", "hung-host,wsl-vm,windows-capabilities,windows-leaks"],
        {"exit": 10, "findings": ["host-unreachable:down-host"], "checks_state": {"wsl-vm": "unknown", "windows-capabilities": "unknown", "windows-leaks": "unknown", "hung-host": "finding"}},
        env=env)], servers=servers))
    out.append(scenario("windows-d3-no-windows-host-skips", [run(common(root, p.root) + sel, {
        "exit": 0, "findings": [], "checks_state": {"wsl-vm": "skipped", "windows-capabilities": "skipped", "windows-leaks": "skipped"}})]))
    # the cards and the document a dashboard reads
    cap = os.path.join(root, "captured-feed-windows.json")
    lk = ["lookout.enabled=true", "lookout.transport=local", "lookout.target=" + os.path.join(HERE, "shims", "lookout-capture")]
    feed_env = dict(env, INFIRMARY_CAPTURE=cap)
    out.append(scenario("windows-d3-cards-flags-and-the-windows-block-in-doctor-json", [run(
        args([host("win2-hot", "hung-pc", guest={"ssh": "guest-dead", "id": "wsl"}), host("win2-hot", "starved-pc"), host("win2-leaks")], lk) + ["--push"], {
            "exit": 30, "findings": ["host-hung:hung-pc/wsl", "wsl-vm-near-cap:starved-pc", "wsl-vm-spinning:starved-pc", "leaked-farm-processes:win2-leaks"],
            "doctor_json": {"status": "CRITICAL", "flags_host": "starved-pc", "schema": "infirmary.doctor"},
            "flags": {"starved-pc": {"drain": True}, "win2-leaks": {"drain": None}},
            "feed": {"path": cap, "source": "infirmary", "cards": ["does not answer while the host does", "is at 98% of its memory cap", "holds farm processes with no live parent"],
                     "collector_ok": ["wsl-vm", "windows-capabilities", "windows-leaks"], "machine_health": {"starved-pc": "draining"}}}, env=feed_env)]))
    # live repair on changes nothing for any of it: the core has no action of its own and no adapter names one for these findings
    live_on = run(["live", "on", "--by", "fixture", "--set", "host.id=" + HOST], {"exit": 0, "text_has": ["live repair: ON"]}, text=True)
    out.append(scenario("windows-d3-live-repair-on-acts-on-none-of-it", [live_on, run(args([host("win2-leaks"), host("win2-hot", guest={"ssh": "guest-dead", "id": "wsl"})]), {
        "exit": 30, "findings": ["host-hung:win2-hot/wsl", "leaked-farm-processes:win2-leaks"], "repairs": [],
        "json_has": {"repair_mode": "live-repair"}, "log_lacks": ["repair", "refusal", "plan"]}, env=env)]))
    # the same checks write nothing under --no-write
    out.append(scenario("windows-d3-no-write-leaves-nothing-behind", [run(args([host("win2-leaks")]) + ["--no-write"], {"exit": 30, "state_empty": True}, env=env)]))
    return out


def platform_scenarios(root):
    """Catalogue 23 to 27: the Windows platform under the lanes (firmware virtualization, hypervisor launch failures, a pending restart,
    Windows Update restarts, the update policy), read from this machine's own Windows side through a fake `powershell` and from a Windows
    host inside the ssh probe. Every answer is SYNTHETIC, written here field for field in the shape `windows/platform.ps1` prints; the
    vendor names are invented. Times are on the answering host's own clock (`now`), never this machine's."""
    now = 1790000000
    answers = os.path.join(root, "platform-answers")
    probes = os.path.join(root, "probes-platform")
    base_probe = json.loads(open(os.path.join(HERE, "recorded", "windows-probe.v2.real.txt")).read()[len("INFIRMARY "):])

    def answer(edit=None, boot=None):
        boot = now - 3 * 86400 if boot is None else boot
        d = {"v": 1, "computer": "FIXTURE-PC", "now": now, "boot": boot, "tz_offset_min": -420,
             # a running hypervisor hides the firmware flag from the processor query: false here is normal, not a finding
             "virt": {"hypervisor_present": True, "firmware_enabled": False, "vbs_status": 2, "vm_platform": 1},
             "distros": {"v2": 1, "v1": 0, "service": "Running"},
             "events": {"read": True, "hypervisor": [{"at": boot + 1, "id": 1}], "vsm_failed": [], "boots": [boot + 1],
                        "restarts": [{"at": boot - 40, "by": "TrustedInstaller.exe", "reason": "Operating System: Upgrade (Planned)", "code": "0x80020003", "kind": "restart"}],
                        "unexpected": [], "updates": [{"at": boot - 300, "id": 19, "title": "Cumulative Update for Windows (KB0000001)", "err": None},
                                                      {"at": boot - 280, "id": 19, "title": "Contoso System Driver Update (1.0.1)", "err": None}]},
             "reboot": {"read": True, "strong": [], "weak": []},
             "policy": {"ux": {"ActiveHoursStart": 8, "ActiveHoursEnd": 17}, "policy": {}, "au": {}, "driver_search": {"SearchOrderConfig": 1},
                        "paused": {"PausedQualityStatus": 0, "PausedFeatureStatus": 0}}}
        if edit:
            edit(d)
        return d

    def save_local(name, d):
        return write(os.path.join(answers, name + ".txt"), "INFIRMARY_PLATFORM " + json.dumps(d, separators=(",", ":")) + "\n")

    def save_host(alias, d, vm_up=True):
        pr = copy.deepcopy(base_probe)
        if not vm_up:
            pr.update({"vmmem_n": 0, "vmmem_mb": 0, "vmmem_cpu_pct": None, "wsl_service": "Stopped"})
        write(os.path.join(probes, alias + ".txt"), "INFIRMARY " + json.dumps(pr, separators=(",", ":")) + "\n")
        write(os.path.join(probes, alias + ".platform.txt"), "INFIRMARY_PLATFORM " + json.dumps(d, separators=(",", ":")) + "\n")

    # the outage: Windows Update restarts the machine with a firmware and platform driver update; the next boot has no hypervisor
    def outage(d):
        b = d["boot"]
        d["virt"].update({"hypervisor_present": False, "firmware_enabled": False, "vbs_status": 0})
        d["events"].update({"hypervisor": [{"at": b + 1, "id": 41}, {"at": b - 86400, "id": 1}], "vsm_failed": [b + 1], "boots": [b + 1, b - 86400],
                            "restarts": [{"at": b - 60, "by": "MoUsoCoreWorker.exe", "reason": "Operating System: Service pack (Planned)", "code": "0x80020010", "kind": "restart"}],
                            "updates": [{"at": b - 70, "id": 19, "title": "Contoso System Driver Update (26.8.29)", "err": None},
                                        {"at": b - 66, "id": 20, "title": "Fabrikam SoftwareComponent Driver Update (2.0.1)", "err": "0x80246007"},
                                        {"at": b - 62, "id": 19, "title": "Contoso - Firmware - 1.40", "err": None},
                                        {"at": b - 3000, "id": 19, "title": "Cumulative Update for Windows (KB0000002)", "err": None}]})

    def recovered(d):       # it failed at the boot before this one and is running again since
        b = d["boot"]
        d["events"].update({"hypervisor": [{"at": b + 1, "id": 1}, {"at": b - 3600 + 1, "id": 41}], "vsm_failed": [b - 3600 + 1], "boots": [b + 1, b - 3600 + 1],
                            "restarts": [{"at": b - 120, "by": "StartMenuExperienceHost.exe", "reason": "Other (Unplanned)", "code": "0x0", "kind": "restart"},
                                         {"at": b - 3600 - 60, "by": "MoUsoCoreWorker.exe", "reason": "Operating System: Service pack (Planned)", "code": "0x80020010", "kind": "restart"}],
                            "updates": [{"at": b - 3600 - 70, "id": 19, "title": "Contoso System Driver Update (26.8.29)", "err": None}]})

    def no_wsl(d):
        d["virt"].update({"hypervisor_present": False, "firmware_enabled": False, "vbs_status": 0, "vm_platform": 2})
        d["distros"].update({"v2": 0, "service": ""})
        d["events"]["hypervisor"] = []

    def fw_on_no_hv(d):
        d["virt"].update({"hypervisor_present": False, "firmware_enabled": True, "vbs_status": 0, "vm_platform": 2})
        d["events"]["hypervisor"] = []

    def pending(d):
        d["reboot"].update({"strong": ["windows-update", "servicing"], "weak": ["file_renames"]})
        d["events"]["updates"].append({"at": now - 3600, "id": 19, "title": "Contoso System Driver Update (26.9.1)", "err": None})

    def soft_only(d):
        d["reboot"]["weak"] = ["file_renames", "computer_rename"]

    def unreadable(d):
        d["reboot"] = {"read": False, "strong": [], "weak": []}

    def people_and_crashes(d, crash):
        d["events"]["restarts"] = [{"at": now - 7200, "by": "StartMenuExperienceHost.exe", "reason": "Other (Unplanned)", "code": "0x0", "kind": "restart"},
                                   {"at": now - 5000, "by": "shutdown.exe", "reason": "No title for this reason could be found", "code": "0x800000ff", "kind": "restart"}]
        d["events"]["unexpected"] = [{"at": now - 3000, "id": 6008}, {"at": now - 2990, "id": 41}] if crash else []

    def guarded_policy(d):
        d["policy"]["policy"] = {"SetActiveHours": 1, "ActiveHoursStart": 6, "ActiveHoursEnd": 23, "ExcludeWUDriversInQualityUpdate": 1,
                                 "DeferQualityUpdates": 1, "DeferQualityUpdatesPeriodInDays": 7}
        d["policy"]["au"] = {"NoAutoRebootWithLoggedOnUsers": 1}

    loc = {n: save_local(n, answer(e, boot=b)) for n, e, b in (
        ("healthy", None, None), ("outage", outage, now - 7200), ("recovered", recovered, now - 5 * 3600), ("no-wsl", no_wsl, None), ("fw-on-no-hv", fw_on_no_hv, None),
        ("pending", pending, now - 2 * 86400), ("soft-only", soft_only, None), ("unreadable", unreadable, None),
        ("people", lambda d: people_and_crashes(d, False), None), ("crashes", lambda d: people_and_crashes(d, True), None), ("policy", guarded_policy, None))}
    save_host("win2-plat-ok", answer())
    save_host("win2-plat-outage", answer(outage, boot=now - 7200), vm_up=False)
    write(os.path.join(probes, "win2-plat-older.txt"), "INFIRMARY " + json.dumps(base_probe, separators=(",", ":")) + "\n")    # an older Infirmary's probe: no platform block

    p = FakeProc(os.path.join(root, "proc-platform"))
    shims = os.path.join(HERE, "shims")
    ids = ["firmware-virtualization", "hypervisor-launch", "pending-reboot", "update-restarts", "update-policy"]
    sel = ["--only", ",".join(ids)]
    all_ok = {i: "ok" for i in ids}

    def local(name, os_kind="wsl", extra=(), only=sel):
        return common(root, p.root, ["doctor.platform=" + os_kind, "doctor.windows.platform.powershell=" + os.path.join(shims, "fake-powershell")] + list(extra)) + only

    def lenv(name):
        return {"INFIRMARY_PLATFORM_ANSWER": loc.get(name, os.path.join(answers, "missing.txt"))}
    env = {"PATH": os.pathsep.join([shims, os.environ.get("PATH", "")]), "INFIRMARY_PROBES": probes}

    def host(alias, **kw):
        return dict({"id": alias, "ssh": alias, "os": "windows"}, **kw)

    def remote(hosts, extra=(), only=sel):
        return common(root, p.root, ["doctor.hosts=" + json.dumps(hosts), "doctor.ssh_timeout_s=2"] + list(extra)) + only
    me = HOST
    out = []
    # a healthy machine seen from its own WSL guest: quiet, the processor's firmware flag (false under a running hypervisor) is not a finding
    out.append(scenario("platform-healthy-wsl-guest-is-quiet-and-the-firmware-flag-under-a-hypervisor-is-normal", [run(local("healthy"), {
        "exit": 0, "findings": [], "checks_state": all_ok, "flags": {me: {"drain": None}},
        "json_has": {"windows.%s.reboot" % me: {"pending": False, "due_epoch": None, "source": None}, "windows.%s.platform.seen_from" % me: "local",
                     "windows.%s.virtualization.hypervisor_present" % me: True, "windows.%s.virtualization.firmware_enabled" % me: False,
                     "windows.%s.platform.wsl_expected" % me: True, "windows.%s.update_policy.drivers" % me: "come through Windows Update"}}, env=lenv("healthy"))]))
    # the outage on a Windows host with a WSL guest: firmware off, the launch failure at this boot, the restart and its driver updates, WSL's own error
    outage_hosts = [host("win2-plat-outage", guest={"ssh": "guest-nohv", "id": "wsl"})]
    out.append(scenario("platform-the-outage-firmware-off-after-a-driver-update-restart", [
        run(remote(outage_hosts, only=["--only", "wsl-vm," + ",".join(ids)]), {
            "exit": 30, "findings": ["firmware-virtualization-off:win2-plat-outage", "hypervisor-launch-failed:win2-plat-outage", "update-restart:win2-plat-outage",
                                     "wsl-not-running:win2-plat-outage/wsl"],
            "flags": {"win2-plat-outage": {"drain": True, "hung": None}},
            "evidence_has": ["HCS_E_HYPERV_NOT_INSTALLED", "Hyper-V-Hypervisor 41", "MoUsoCoreWorker.exe", "Contoso - Firmware - 1.40", "Fabrikam SoftwareComponent Driver Update (2.0.1) (failed, 0x80246007)",
                             "Kernel-Boot 124", "the next boot came up without the hypervisor", "(Operating System: Service pack (Planned)), outside active hours"],
            "json_has": {"windows.win2-plat-outage.virtualization.hypervisor_present": False, "windows.win2-plat-outage.platform.seen_from": "ssh",
                         "windows.win2-plat-outage.wsl.vm_running": False, "windows.win2-plat-outage.reboot.pending": False}}, env=env),
        run(remote(outage_hosts, only=["--only", "wsl-vm,firmware-virtualization", "-v"]), {
            "exit": 30, "text_has": ["virtualization is off in the firmware of win2-plat-outage", "WSL says HCS_E_HYPERV_NOT_INSTALLED", "enable Intel Virtualization Technology"]},
            env=env, text=True),
        run(remote(outage_hosts, only=["--only", "firmware-virtualization"]), {      # virtualization off in firmware drains the host on its own
            "exit": 30, "findings": ["firmware-virtualization-off:win2-plat-outage"], "flags": {"win2-plat-outage": {"drain": True}}}, env=env)]))
    # the same outage read on Windows itself (the Infirmary run natively): WSL 2 is expected because a WSL 2 distribution is registered
    out.append(scenario("platform-the-outage-read-natively-on-windows", [run(local("outage", os_kind="windows"), {
        "exit": 30, "findings": ["firmware-virtualization-off:%s" % me, "hypervisor-launch-failed:%s" % me, "update-restart:%s" % me],
        "flags": {me: {"drain": True}}, "json_has": {"windows.%s.platform.wsl_expected_because" % me: "1 WSL 2 distribution(s) registered"}}, env=lenv("outage"))]))
    # twins: no hypervisor and virtualization off, but no WSL 2 guest is expected; firmware on but the hypervisor off is a different finding
    out.append(scenario("platform-no-hypervisor-but-no-wsl2-expected-is-quiet", [run(local("no-wsl", os_kind="windows"), {
        "exit": 0, "findings": [], "checks_state": all_ok, "json_has": {"windows.%s.platform.wsl_expected" % me: False}}, env=lenv("no-wsl"))]))
    out.append(scenario("platform-firmware-on-but-no-hypervisor-is-the-feature-or-launch-type", [run(local("fw-on-no-hv", os_kind="windows", extra=["doctor.windows.platform.wsl_expected=true"]), {
        "exit": 30, "findings": ["hypervisor-not-running:%s" % me], "evidence_has": ["Virtual Machine Platform feature: disabled"], "flags": {me: {"drain": True}},
        "json_has": {"windows.%s.platform.wsl_expected_because" % me: "doctor.windows.platform.wsl_expected is true"}}, env=lenv("fw-on-no-hv"))]))
    # recovered: the failure of the boot before is a warning that names the Windows Update restart behind it; outside the window it is history
    out.append(scenario("platform-a-recovered-launch-failure-warns-then-ages-out", [
        run(local("recovered"), {"exit": 10, "findings": ["hypervisor-launch-failed-recently:%s" % me, "update-restart:%s" % me],
                                 "evidence_has": ["the first failed boot followed a restart by MoUsoCoreWorker.exe for Windows Update", "Contoso System Driver Update (26.8.29)"],
                                 "flags": {me: {"drain": None}}}, env=lenv("recovered")),
        run(local("recovered", extra=["doctor.windows.platform.hypervisor_recent_h=1", "doctor.windows.platform.restart_recent_h=1"]), {
            "exit": 0, "findings": [], "checks_state": all_ok}, env=lenv("recovered"))]))
    # a restart pending: the fact in the shape Harbor reads, the driver update staged for it; softer signs alone are not a pending restart; unreadable keys are unknown
    out.append(scenario("platform-a-pending-restart-is-the-fact-harbor-reads", [run(local("pending"), {
        "exit": 10, "findings": ["reboot-pending:%s" % me], "evidence_has": ["Contoso System Driver Update (26.9.1)", "Windows Update, Windows servicing", "files wait to be replaced"],
        "json_has": {"windows.%s.reboot" % me: {"pending": True, "due_epoch": None, "source": "Windows Update"},
                     "windows.%s.reboot_detail.staged_driver_updates" % me: ["Contoso System Driver Update (26.9.1)"]}}, env=lenv("pending"))]))
    out.append(scenario("platform-softer-signs-alone-are-not-a-pending-restart", [run(local("soft-only"), {
        "exit": 0, "findings": [], "checks_state": all_ok, "json_has": {"windows.%s.reboot.pending" % me: False, "windows.%s.reboot_detail.weak" % me: ["file_renames", "computer_rename"]}},
        env=lenv("soft-only"))]))
    out.append(scenario("platform-unreadable-restart-keys-are-unknown-not-fine", [run(local("unreadable"), {
        "exit": 50, "findings": [], "checks_state": dict(all_ok, **{"pending-reboot": "unknown"}), "json_has": {"windows.%s.reboot.pending" % me: None}}, env=lenv("unreadable"))]))
    # restarts: a person's and a script's restart are not Windows Update's; an unexpected stop is its own finding
    out.append(scenario("platform-restarts-by-a-person-or-a-script-are-left-alone", [run(local("people"), {
        "exit": 0, "findings": [], "checks_state": all_ok}, env=lenv("people"))]))
    out.append(scenario("platform-an-unexpected-restart-warns", [run(local("crashes"), {
        "exit": 10, "findings": ["unexpected-restart:%s" % me], "evidence_has": ["EventLog 6008", "Kernel-Power 41"]}, env=lenv("crashes"))]))
    # the policy is information: never a finding, read into the document
    out.append(scenario("platform-the-update-policy-is-information", [run(local("policy"), {
        "exit": 0, "findings": [], "checks_state": all_ok,
        "json_has": {"windows.%s.update_policy.drivers" % me: "excluded from Windows Update by policy", "windows.%s.update_policy.active_hours" % me: [6, 23],
                     "windows.%s.update_policy.active_hours_source" % me: "policy", "windows.%s.update_policy.defer_quality_days" % me: 7,
                     "windows.%s.update_policy.no_auto_restart_with_user_signed_in" % me: True}}, env=lenv("policy"))]))
    # cannot read: no interop (powershell.exe does not start), an older Infirmary's probe, nothing to read at all
    out.append(scenario("platform-no-interop-is-unknown-and-says-why", [run(local("missing"), {
        "exit": 50, "findings": [], "checks_state": {i: "unknown" for i in ids}, "text_has": ["Windows interop may be off in this guest"]}, env=lenv("missing"), text=True)]))
    out.append(scenario("platform-an-older-probe-answer-is-unknown-not-fine", [run(remote([host("win2-plat-older")]), {
        "exit": 50, "findings": [], "checks_state": {i: "unknown" for i in ids}}, env=env)]))
    out.append(scenario("platform-nothing-to-read-on-linux-skips", [run(common(root, p.root) + sel, {
        "exit": 0, "findings": [], "checks_state": {i: "skipped" for i in ids}})]))
    out.append(scenario("platform-a-healthy-windows-host-over-ssh-is-quiet", [run(remote([host("win2-plat-ok", guest={"ssh": "guest-ok", "id": "wsl"})]), {
        "exit": 0, "findings": [], "checks_state": all_ok, "json_has": {"windows.win2-plat-ok.reboot.pending": False, "windows.win2-plat-ok.platform.wsl_expected": True}}, env=env)]))
    # cards, live repair on acting on none of it, and --no-write
    cap = os.path.join(root, "captured-feed-platform.json")
    lk = ["lookout.enabled=true", "lookout.transport=local", "lookout.target=" + os.path.join(shims, "lookout-capture")]
    out.append(scenario("platform-cards-for-the-outage", [run(remote(outage_hosts, lk) + ["--push"], {
        "exit": 30, "feed": {"path": cap, "source": "infirmary", "cards": ["virtualization is off in the firmware", "the hypervisor failed to launch at this boot", "Windows Update restarted"],
                             "collector_ok": ["firmware-virtualization", "update-policy"], "machine_health": {"win2-plat-outage": "draining"}}}, env=dict(env, INFIRMARY_CAPTURE=cap))]))
    live_on = run(["live", "on", "--by", "fixture", "--set", "host.id=" + HOST], {"exit": 0, "text_has": ["live repair: ON"]}, text=True)
    out.append(scenario("platform-live-repair-on-acts-on-none-of-it", [live_on, run(remote(outage_hosts), {
        "exit": 30, "repairs": [], "json_has": {"repair_mode": "live-repair"}, "log_lacks": ["repair", "refusal", "plan"]}, env=env)]))
    out.append(scenario("platform-no-write-leaves-nothing-behind", [run(local("outage", os_kind="windows") + ["--no-write"], {"exit": 30, "state_empty": True}, env=lenv("outage"))]))
    return out


def unknown_scenarios(root):
    """Unknown is a visible card with the exact reason, never a blank: one scenario per reason class (could-not-read, check-broke, overran;
    rule-unknown is the adapter's, whose findings can be UNKNOWN), and the healthy look-alike that differs by one fact (the reading exists)."""
    shim = os.path.join(HERE, "shims", "lookout-capture")
    lk = ["lookout.enabled=true", "lookout.transport=local", "lookout.target=" + shim]
    empty = os.path.join(root, "proc-unknown-empty")
    os.makedirs(empty, exist_ok=True)
    good = FakeProc(os.path.join(root, "proc-unknown-good"))
    good.add(1, 0, ["/sbin/init"], 900000)
    sick = FakeProc(os.path.join(root, "proc-unknown-sick"))
    sick.memory(total_gb=16, avail_pct=3, swap_gb=8, swap_used_pct=95, psi_full=20)
    sick.add(1, 0, ["/sbin/init"], 900000)
    out = []

    def args(proc, name, extra=()):
        cap, cards = os.path.join(root, "captured-feed-unknown-%s.json" % name), os.path.join(root, "cards-unknown-" + name)
        return common(root, proc, lk + list(extra)) + ["--push", "--card", cards + os.sep], cap, os.path.join(cards, "infirmary.json")

    # could not read: no memory reading here. One card with the reason, the same one card on the next run, and a tile that does not say "all clear"
    a, cap, card = args(empty, "read")
    exp = {"exit": 50, "findings": [], "checks_state": {"memory-swap": "unknown"}, "card_valid": [card],
           "feed": {"path": cap, "source": "infirmary", "cards": ["memory-swap: unknown (could-not-read)"], "card_severity": "info", "card_ids": {"unknown:memory-swap:could-not-read": 1},
                    "card_detail_has": ["no memory reading on this OS", "not a pass"], "collector_error_has": {"memory-swap": "could-not-read: no memory reading"}},
           "file_has": {card: ['"status": "unknown"', "1 unknown", "could-not-read", "no memory reading on this OS"]}, "file_lacks": {card: ["all clear"]}}
    out.append(scenario("unknown-an-unreadable-input-is-a-card-with-its-reason", [run(a, exp, env={"INFIRMARY_CAPTURE": cap}), run(a, exp, env={"INFIRMARY_CAPTURE": cap}),
                                                                                  run(common(root, empty), {"exit": 50, "text_has": [
                                                                                      "unknown (1; the Infirmary could not decide, which is not a pass)", "memory-swap · could-not-read · no memory reading on this OS",
                                                                                      "next: make the source the reason names readable", "1 unknown · exit 50"]}, text=True)]))
    # the check breaks: its error is the reason
    a, cap, card = args(good.root, "broke", ["doctor.disk.paths=5"])
    out.append(scenario("unknown-a-check-that-breaks-is-a-card-with-its-error", [run(a, {
        "exit": 50, "findings": [], "checks_state": {"disk-exhaustion": "error"}, "card_valid": [card],
        "feed": {"path": cap, "source": "infirmary", "cards": ["disk-exhaustion: unknown (check-broke)"], "card_ids": {"unknown:disk-exhaustion:check-broke": 1}, "card_detail_has": ["TypeError"],
                 "collector_error_has": {"disk-exhaustion": "check-broke: TypeError"}},
        "file_has": {card: ["1 unknown", "check-broke", "TypeError"]}, "file_lacks": {card: ["all clear"]}}, env={"INFIRMARY_CAPTURE": cap})]))
    # a probe of another host that does not finish within its budget
    shim_path = os.pathsep.join([os.path.join(HERE, "shims"), os.environ.get("PATH", "")])
    a, cap, card = args(good.root, "overran", ["doctor.hosts=" + json.dumps([{"id": "hung-host", "ssh": "hung-host", "os": "linux"}]), "doctor.ssh_timeout_s=10", "doctor.remote_budget_ms=300"])
    out.append(scenario("unknown-a-probe-that-overruns-is-a-card-with-its-budget", [run(a + ["--only", "hung-host"], {
        "exit": 50, "findings": [], "checks_state": {"hung-host": "error"}, "card_valid": [card],
        "feed": {"path": cap, "source": "infirmary", "cards": ["hung-host: unknown (overran)"], "card_ids": {"unknown:hung-host:overran": 1}, "card_detail_has": ["did not finish within the remote budget (300 ms)"]},
        "file_has": {card: ["1 unknown", "overran"]}, "file_lacks": {card: ["all clear"]}}, env={"PATH": shim_path, "INFIRMARY_CAPTURE": cap})],
        servers=[{"kind": "silent", "env": "INFIRMARY_PORT_SILENT"}]))
    # an unknown beside a real finding is still said: the tile counts both
    a, cap, card = args(sick.root, "beside", ["doctor.disk.paths=5"])
    out.append(scenario("unknown-beside-a-finding-is-still-said", [run(a, {
        "exit": 30, "findings": ["memory-pressure:" + HOST], "checks_state": {"disk-exhaustion": "error"}, "card_valid": [card],
        "feed": {"path": cap, "source": "infirmary", "cards": ["memory and swap are exhausted", "disk-exhaustion: unknown (check-broke)"], "card_ids": {"unknown:disk-exhaustion:check-broke": 1}},
        "file_has": {card: ['"status": "bad"', "1 finding (1 critical)", "1 unknown"]}}, env={"INFIRMARY_CAPTURE": cap})]))
    # the healthy look-alike: the reading exists, so there is no unknown card, the tile says all clear, and a check that does not apply says why
    a, cap, card = args(good.root, "healthy")
    out.append(scenario("unknown-healthy-look-alike-has-no-unknown-card", [run(a, {
        "exit": 0, "findings": [], "checks_state": {"memory-swap": "ok", "hung-host": "skipped"}, "card_valid": [card], "json_has": {"unknowns": []},
        "feed": {"path": cap, "source": "infirmary", "cards": [], "no_card_id_prefix": ["unknown:"], "collector_ok": ["memory-swap"],
                 "collector_warning_has": {"hung-host": "no other hosts configured"}},
        "file_has": {card: ['"status": "ok"', "all clear"]}, "file_lacks": {card: ["1 unknown", "could-not-read"]}}, env={"INFIRMARY_CAPTURE": cap})]))
    return out


def adapter_scenarios(root):
    """A named adapter that cannot be loaded is an error row, never a silent skip: `check` says so and exits 50, `advise` says the incidents were not
    read, and `install` stores the adapter directories as absolute paths. The look-alike differs by one fact: the adapter is found."""
    good = FakeProc(os.path.join(root, "proc-adapter-good"))
    good.add(1, 0, ["/sbin/init"], 900000)
    sample = os.path.join(HERE, "adapters")
    missing = os.path.join(root, "no-such-adapter-dir")
    gone = common(root, good.root, ["adapter=nosuch", "adapter_paths=" + json.dumps([missing])])
    home = os.path.join(root, "home-adapter")
    os.makedirs(home, exist_ok=True)
    rel = os.path.relpath(sample, os.path.dirname(os.path.dirname(HERE)))     # the runner's working directory is the repository root
    return [
        scenario("adapter-missing-is-an-error-row-and-exit-50", [
            run(gone, {"exit": 50, "findings": [], "checks_state": {"adapter": "error"}, "json_has": {"incidents.not_read": "adapter nosuch not found"}}),
            run(gone, {"exit": 50, "text_has": ["adapter nosuch not found (looked in " + missing, "unknown (1; the Infirmary could not decide, which is not a pass)", "exit 50"]}, text=True)]),
        scenario("adapter-missing-advise-says-the-incidents-were-not-read", [
            run(["advise"] + gone[1:], {"exit": 0, "text_has": ["incidents: not read: adapter nosuch not found", "adapter · check-broke: adapter nosuch not found (looked in " + missing]}, text=True)]),
        scenario("adapter-install-stores-adapter-paths-as-absolute-paths", [
            run(["install", "--home", home, "--adapter", "sample", "--set", "adapter_paths=" + json.dumps([rel]), "--advice-only", "--dry-run"],
                {"exit": 0, "text_has": ["dry run: nothing written, nothing scheduled", '"%s"' % sample], "stdout_lacks": ['"%s"' % rel], "files": {os.path.join(home, "infirmary"): False}}, text=True)]),
        scenario("adapter-found-look-alike-has-no-adapter-row", [
            run(common(root, good.root, ["adapter=sample", "adapter_paths=" + json.dumps([sample])]), {"exit": 0, "findings": [], "json_has": {"incidents.not_read": None}})]),
    ]


def main():
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    scenarios = []
    for fn in (memory_scenarios, beat_scenarios, mac_scenarios, disk_scenarios, host_scenarios, windows_scenarios, platform_scenarios, clobber_scenarios, interop_scenarios,
               scratch_scenarios, ssh_check_scenarios, doc_scenarios, unknown_scenarios, adapter_scenarios):
        scenarios += fn(out)
    fixlib.save(out, scenarios)
    print("wrote %d scenarios under %s" % (len(scenarios), out))


if __name__ == "__main__":
    main()
