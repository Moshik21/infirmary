# The catalogue (detection of items 1 to 28, and the repairs that answer them)

`infirmary check` runs these checks and prints one line per area, like `brew doctor` or `flutter doctor`.
Every finding carries **evidence** (what was read, where), a **plain explanation**, a **how** (the owning
script and its dry-run command, or who decides) and a **recommendation** written for a person. Detection writes to no
home or worktree and signals no process (see [safety.md](safety.md)); the repairs that answer some findings run only while
the operator has live repair on for the home, only through the owner's own command, and are listed in [repairs.md](repairs.md) and in the adapter's README.

A check says `ok` only when it saw something that makes it so ("one worker tree, identity matches", "swap 95% used
but 45% of memory is free"). When it cannot read what it needs it says `unknown`, and when nothing applies it says
`skip` with the reason. Silence is never green.

| # | Check id | Finding kinds | Detects | Needs |
| ---: | --- | --- | --- | --- |
| 2 | `hung-host` | `host-hung`, `host-unreachable`, `host-memory-short` | a configured host that accepts connections but never answers ssh; one that does not answer at all; a Windows host short of memory (available MB, commit charge, the VM's working set, old leaked processes); a guest that does not answer while its host does | `doctor.hosts`, ssh |
| 3 | `control-sockets` | `control-socket-dead` | an ssh control socket with no live master | `doctor.channels.control_sockets` |
| 5 | `clobbered-checkout` | `clobbered-bindings` | a checkout whose dependencies hold another OS's native bindings and none for this one ("Cannot find native binding") | adapter or `doctor.clobber.roots` |
| 9 | `disk-exhaustion` | `disk-low`, `disk-growth` | free space per volume below the thresholds; a volume that will fill within `growth_days` at its current rate; when sick, a size inventory of the known scratch places | core; adapter names the places |
| 10 | `memory-swap`, `orphan-servers` | `memory-pressure`, `orphan-servers` | memory and swap exhausted (the host beat's level when fresh, else the kernel's own numbers); configured servers left running with no parent | core |
| 2 | `wsl-vm` | `host-hung` (a guest that does not answer while its Windows host's VM runs), `wsl-not-running`, `wsl-vm-near-cap`, `wsl-vm-spinning` | the WSL VM seen from the Windows side: the guest's ssh alias silent while `vmmem` is up (hung) or absent (stopped, not hung); the VM at 90% or more of its `.wslconfig` cap (97% and a host short of memory is critical); the VM using 90% of the host's CPUs while the host is short of memory | `doctor.hosts` with `os: windows` (and `guest` for the hung/stopped split), ssh |
| 14 | `windows-capabilities` | `capability-drift` | what a job arriving over ssh gets on a Windows host (login shell and whether its profile loads, graphics adapters and the per-app GPU preference, tools by file version, a signed-in desktop) against what the host's `expect` block declares | `doctor.hosts[].expect`, ssh |
| 16 | `windows-leaks` | `leaked-farm-processes` | servers (`game-server-standalone`) and farm-marked node, chrome and headless browsers with no live parent for 30 minutes or more, with their child trees and memory; counts of bash, sshd and powershell session processes parentless for 12 hours | `doctor.hosts` with `os: windows`, ssh |
| 19 | `wsl-interop` | `wsl-interop-missing` | a WSL guest whose binfmt_misc table has no `WSLInterop` entry (or has it switched off): Windows programs cannot be started from the guest. Unknown when binfmt_misc is not mounted | core; WSL only (skipped elsewhere) |
| 20 | `scratch-leaks` (core), groups named by the adapter | `scratch-leaked`, `tmpfs-full` | entries of a configured group (e.g. headless-browser profiles, test-floor data directories) that nothing uses: untouched for the group's minimum age, named by no live process (command line, working directory, open file) and not owned by a live pid the name carries, adding up to `min_mb`; with sizes, count, oldest age and what was checked. A RAM-backed volume (tmpfs, ramfs) at or above 90% used, and a critical one (97%) flags the host to drain | core: `/proc/mounts`, process table; adapter: the groups |
| 23 | `firmware-virtualization` | `firmware-virtualization-off`, `hypervisor-not-running` | no hypervisor running and virtualization (VT-x / SVM) off in firmware while a WSL 2 guest is expected (critical, sets `drain`); firmware on but the hypervisor still not started | core: this machine's Windows side (WSL guest or Windows) and Windows hosts in `doctor.hosts`, read-only PowerShell |
| 24 | `hypervisor-launch` | `hypervisor-launch-failed`, `hypervisor-launch-failed-recently` | Hyper-V-Hypervisor event 41 at this boot (critical, sets `drain`) or at a recent boot (warning), with the restart that led to it and the driver or firmware updates around that restart | same |
| 25 | `pending-reboot` | `reboot-pending` | the Windows Update or servicing restart key is set, with the active hours and the driver updates staged for that restart; the fact goes to `doctor.json` in the shape a Harbor beat carries | same |
| 26 | `update-restarts` | `update-restart`, `unexpected-restart` | a restart Windows Update asked for (User32 1074) in the last 24 h, inside or outside active hours, with its driver and firmware updates; an unexpected stop (EventLog 6008, Kernel-Power 41) | same |
| 27 | `update-policy` | none (information) | active hours, whether drivers come through Windows Update, deferral, pause, the automatic-update option | same |
| 28 | `ssh-check` | `ssh-check-required` | an ssh alias whose non-interactive login stops at an **additional check** (Tailscale SSH: "requires an additional check" and a login URL): a person must sign in before the server lets anything through, so steers, pushes and job channels over that alias wait silently. One bounded `BatchMode` attempt per alias (the hosts' own probe is shared), sorted into ok, check, refused login, no answer; only the prompt is a finding, with its URL, as the operator's to clear. The URL is never opened and the check never completed. See [ssh-check.md](ssh-check.md) for the aliases it tries and the check period | `doctor.hosts`, `doctor.ssh_check`, ssh |

Each finding kind names the upstream issue or pull request that would make the check unnecessary (`retires_with` in
the finding): when the owner fixes the cause, the check is deleted rather than kept as a second opinion.

**Every check is optional.** `doctor.disabled` takes any check id (`--set doctor.disabled='["ssh-check"]'`), and a check that needs a
declaration or a place to look says `skip` with the reason until it has one (a scratch group, a Windows side to read).

## Thresholds and where they came from

All are settings (`--set doctor.disk.warn_gb=60`); the defaults are first cuts taken from recorded incidents.

| Setting | Default | Meaning |
| --- | --- | --- |
| `doctor.disk.warn_gb` / `crit_gb` | 40 / 15 | free GB per volume (the beat's own defaults); percentages `warn_pct` 15, `crit_pct` 7 also apply |
| `doctor.disk.growth_days` | 3 | warn when the volume would fill within this many days at the last day's rate |
| `doctor.memory.avail_warn_pct` / `avail_crit_pct` | 10 / 5 | available memory as a share of total |
| `doctor.memory.psi_full_warn` / `psi_full_crit` | 5 / 15 | share of the last minute all tasks stalled on memory (Linux) |
| `doctor.memory.swap_warn_pct` / `swap_crit_pct` | 70 / 90 | swap in use counts only **together with** scarce memory or a stall; swap alone is cold pages |
| `doctor.windows.avail_crit_mb` | 1024 | the 2026-10 incident that hung a WSL host started at about 1 GB available |
| `doctor.windows.wsl.cap_warn_pct` / `cap_crit_pct` / `cpu_warn_pct` | 90 / 97 / 90 | the VM's working set against its `memory=` cap (half the host's RAM when none is set); critical only together with a host short of memory, because cached files fill a VM to its cap without harm. CPU is the VM's share of all the host's CPUs over a sample of under a second, and counts only on a host short of memory |
| `doctor.windows.leaks.min_age_min` | 30 | a parentless process younger than this may be a job starting up |
| `doctor.windows.leaks.servers` / `farm` / `farm_markers` | `game-server-standalone.exe` / node, chrome, headless shells / `--headless`, `--remote-debugging-port`, `ms-playwright`, `puppeteer`, `chrome-headless-shell` | a parentless `servers` process is a leak; a parentless `farm` process only when its command line or executable path holds a marker (so a person's own browser or dev server is never listed). Add your farm's directories to `farm_markers` |
| `doctor.windows.leaks.sessions` / `session_hours` / `session_warn` / `crit_mb` | bash, sshd, powershell / 12 / 20 / 4096 | session processes that lost their parent are counted, not listed; 20 or more warn; leaked memory of 4 GB or more is critical |
| `doctor.windows.capabilities.tools` | node, git, python, chrome, spacetime | looked up by file version (on `PATH`, else in the App Paths registry); nothing is run to learn a version. A Store `python.exe` stub counts as not installed |
| `doctor.scratch.min_age_s` / `min_mb` | 1800 / 200 | a scratch entry is leaked only after this long untouched, used by no live process; a group counts once its leaked entries hold this much (a group can set its own). The adapter's groups: headless-browser profiles (20 minutes), test-floor data directories (30 minutes, owner pid read from the name) under the temp directory |
| `doctor.scratch.tmpfs_warn_pct` / `tmpfs_crit_pct` / `tmpfs_min_mb` | 90 / 97 / 64 | a RAM-backed volume this full is a finding, a critical one also flags the host to drain; smaller volumes are not watched. A full tmpfs costs memory as well as space |
| `doctor.scratch.groups` | none | `[{"label", "glob" (a pattern or a list), "min_age_s", "min_mb", "owner_pid_re"}]`: where a job may leave scratch. `owner_pid_re` has one group that captures the pid the entry's name carries; a live pid means in use |
| `doctor.for_s.<kind>` | 60 to 600 | a finding must be seen this long before it counts (`pending` until then); `clear_s` (300) forgets it once absent. Defaults: duplicate or stale workers, orphan lanes and memory pressure 120 s, duplicate watchers and host probes 60 s, a gate ahead of its lane 600 s (a running pipeline does that), a done line without its commit 120 s (the commit is often seconds away), a farm process that looks idle 300 s, a RAM-backed volume 60 s, everything else at once |
| `doctor.ssh_check.aliases` / `from_hosts` / `from_ssh_config` / `ssh_config` / `exclude` / `max_aliases` | none / on / off / `~/.ssh/config` / none / 24 | which ssh aliases the additional-check probe tries besides `doctor.hosts` and their guests; see [ssh-check.md](ssh-check.md) |
| `doctor.windows.platform.*` | `local` auto, `lookback_h` 72, `hypervisor_recent_h` 24, `restart_recent_h` 24, `update_window_h` 12, `timeout_s` 30 | catalogue 23 to 27: also read this machine's own Windows side; how far back the System event log is read; how recent a launch failure or a Windows Update restart must be to be reported (the incident behind them was over within the day); updates installed this long before a restart count as part of it. All of them in [windows-platform.md](windows-platform.md#settings-doctorwindowsplatform) |
| `doctor.incidents.enabled` / `max_rows` / `tail_kb` / `ledger_max_kb` | true / 50 / 512 / 8192 | incident rows ([incidents.md](incidents.md)): off writes none; rows made per run (the rest counted as omitted); how much of the ledger's end is read to tell which ids it holds; the size at which the ledger refuses new rows (it is never rotated) |
| `doctor.for_s.incident-unreadable` | 120 | an input an incident rule could not read must stay unreadable this long before it is a row; the incident rules themselves confirm at once, because their own records date them |
| `doctor.tick_budget_ms` | 2000 | local checks are held to this; the cost is reported in `doctor.json` (`cost.incidents_ms` is the incident fold) |
| `advice.budget.*` | `wall_s` 30, `cpu_s_per_hour` 30, `load_per_core_max` 0.8, `battery_min_pct` 30, `battery_wall_s` 10 | what an advice run may cost and when it steps aside, saying so: [advice.md](advice.md#what-a-run-costs-and-when-it-steps-aside) |
| `doctor.repair.*` | | rate limits, breakers, flap detection and plan expiry for live repair: [repairs.md](repairs.md#settings-doctorrepair) |

## Per-OS reach

Linux and WSL read `/proc` and `git`; macOS runs `ps -axo`, `vm_stat` and `sysctl` (parsed and tested against
recorded or documented output, **not yet run on a Mac**); a Windows host is probed from another machine with
one read-only PowerShell command over ssh, nothing installed there (one probe a tick answers `hung-host`, `wsl-vm`, `windows-capabilities`
and `windows-leaks`; the script travels as one line of base64 on standard input and runs in memory). Run natively on Windows, the process and
memory checks report `unknown` rather than guess. The platform checks (23 to 27) also read this machine's own Windows side: from a WSL guest
through `powershell.exe` (interop), or on Windows itself; for a Windows host in `doctor.hosts` their script rides on the same one probe.

### The Windows-side checks (catalogue 2, 14 and 16)

A Windows host in `doctor.hosts` is asked once per tick. A host that does not answer is `unknown` in all three and a `hung-host` finding; a
probe answer from an older Infirmary (no `v`) is `unknown` for what it lacks. Each host's `expect` block is optional, and only a declaration can
drift:

```
{"id": "win-box", "ssh": "win-box", "os": "windows", "guest": {"ssh": "win-box-wsl", "id": "wsl"},
 "expect": {"shell": "powershell", "login_profile": "clean", "gpu": ["RTX"], "gpu_preference": {"chrome.exe": "high"},
            "tools": {"node": ">=20", "git": true}, "desktop": true}}
```

`gpu` lists substrings that each must match an adapter; `gpu_preference` is the per-app Windows setting (`high`, `power`, `system`);
`tools` takes `true` (present) or `">=N.N"`; `desktop` wants an explorer in a user session; `login_profile: "clean"` wants the login
shell to start without errors. Everything seen is also in `doctor.json` under `windows.<host>` ([findings.md](findings.md)).
Nothing here repairs anything: ending a process on a Windows host is a person's call, by pid and start time.

### The scratch-leaks check (catalogue 20)

Read-only against this machine. `scratch-leaks` reads the links of `/proc/<pid>/fd` and `cwd` (never the files they point at). None of that exists on macOS
(`ps` only): the check says `skip` or `unknown` there, and falls back to command lines alone and says so.

### The Windows platform (catalogue 23 to 27)

Firmware virtualization, hypervisor launch failures, a pending restart, Windows Update restarts and the update policy, from the incident in
which a Windows Update driver restart left virtualization off in firmware and every WSL lane on the machine down for hours. What each reads,
the look-alikes that are not findings, the diagnosis recipe and the hand-off of the pending restart to Harbor are in
[windows-platform.md](windows-platform.md). Read-only and advice-only: the Infirmary changes no firmware, boot, policy, service or scheduled-task
setting and installs nothing.

## Not yet covered

The guest-beat comparison for a hung WSL seen from the Windows side (needs the guest's beat
to be readable from another machine), and a second mate's quiet parent channel are later work. `capability_drift` in the flags is `null`
(not checked: the host declares nothing), `false` once a declared host matched, `true` on drift, never `false` by silence. Repairs exist only
where an owner command exists and the adapter names it: the core has no actions of its own.
