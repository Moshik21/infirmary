# Safety model

| Mode | Behaviour |
| --- | --- |
| `off` | the tool prints that it is off and exits 0; also the effect of the kill switch |
| `advise` (default), shown as **advice-only** | detect, explain, log, optionally publish; never act. A repair is reported as what it *would* do |
| `repair` (Infirmary only), shown as **live-repair** | also run the tier-1 repairs, each by calling its owner's command after two readings; write tier-2 plans. Exists only while an operator has turned live repair on for the home (below); it is not a setting |

Tiers, gates, plans and `approve` are in [repairs.md](repairs.md); how the `repair` mode is switched on, per home, is
[Live repair: the operator's switch](#live-repair-the-operators-switch) below. The short version: the Infirmary never signals a process,
deletes a file, moves a branch or edits a record itself. It calls an owner's own command, with the arguments the owner's
dry run proposed, only where the owner proves the target safe, and only after it re-checked everything immediately
before acting. Anything riskier is an inert plan file that a person approves with `infirmary approve`.

**Kill switch.** Any of: the file `<state dir>/off` (`infirmary off` / `on`), `INFIRMARY=off` in
the environment, or `mode: "off"` in the config. It is read on every run, again right before a repair's second reading,
and again right before the act: a switch thrown while a repair is in flight stops it. `approve` honours it too.

**What is written.** Only the state directory (default `~/.local/state/infirmary`, `~/Library/Application
Support/infirmary` on macOS, `%LOCALAPPDATA%\infirmary` on Windows; override with `state_dir` or
`INFIRMARY_STATE`): `actions.jsonl` (one JSON line per explanation, replay, check, switch change, live-repair toggle change, repair, refusal, plan,
approval and breaker reset; rotated at 1 MiB), `push.seq` and `push-doctor.seq`, `off`, `live-repair.json` (only the operator's `live on` and `live off` write it), and for the Infirmary `doctor.json`,
`seen.json` (when each finding was first and last seen, for the for/clear durations), `incidents.jsonl` (the append-only incident ledger: one line per confirmed incident, opened for append under a file lock, never truncated, rotated or edited; [incidents.md](incidents.md)) and `incidents.json` (what is open now), `judged.jsonl` (the append-only judged-alarm ledger: a verdict row per explained alarm and an outcome row once its window has passed; the same discipline, and `uninstall` leaves it; [judged.md](judged.md)), `disk-history.json` (a bounded list
of used-space samples), `repairs.json` (its runs, for the rate limits and breakers), `plans/` and `repair.lock`. `check
--no-write` writes none of them and acts on nothing. A failure to write never changes a verdict. `advise` also writes
`advice.json`, `advice.txt` and `card.json` there, and the card to `advice.card` when that is set. An incident row carries only what the
rule read (a lane's name and branch, a record's name, its age, a git count, a next step as text): never a command line, an environment or a credential.

**The per-home install** (`infirmary install`, [advice.md](advice.md)) is the one command that writes outside a state directory,
and only into the invoking user's own scheduler. On Linux and WSL it adds one line, ending in `# infirmary advice <dir>`, to the
user's crontab, through `crontab -l` and `crontab -`. Every other line is kept byte for byte, and the crontab as it was is saved
beside the install. On macOS it writes one LaunchAgent plist under `~/Library/LaunchAgents` and loads it with `launchctl bootstrap
gui/<uid>`; the plist holds a `PATH` (the user's own home expanded at install time) and `ProcessType` `Standard`. It never uses `sudo`, never touches a system scheduler, service or daemon, and edits no shared configuration. Its state
directory is inside the home it serves. `uninstall` removes exactly that line or agent, plus the files the install and the advice
run wrote. The install has no repair option and writes no live-repair record. What it schedules, `advise`, runs the check in advise
mode whatever the toggle says.

**What is read.** Whatever the source is configured to read, as the invoking user, with no privilege. The Infirmary reads
`/proc` (or runs `ps`, `vm_stat`, `sysctl` on macOS), the adapter's home, pool, remote-job and gate state, and a
checkout's `node_modules` *directory names*. From another process's environment it keeps only the named variables it
needs (a home or state path), never the rest. From a process's descriptors and working
directory it reads only where the links point (to tell whether scratch or a slot file is still in use), never the files. Process arguments are not printed: a finding shows the executable, the
pid, the start time and a short hash of the command line, so an identity can be checked without exposing a secret.
The advice run's budget reads the machine's load (`/proc/loadavg`, `/proc/cpuinfo`, or `sysctl vm.loadavg` and `hw.ncpu` on a Mac) and its power state (`/sys/class/power_supply`, or `pmset -g batt`), and writes one small file of its own runs (`advice-cost.json`) into the state directory ([advice.md](advice.md#what-a-run-costs-and-when-it-steps-aside)). Adapters open other tools' databases read-only. Nothing reads or prints a credential; the Lookout push
token comes from a file or an environment variable, never argv.

**Network.** None, except an HTTP POST to the Lookout host in `lookout.target` when `lookout.enabled`
and `--push` are both set, and, for each host you list in `doctor.hosts`, one read-only ssh probe (`BatchMode`, a
connect timeout, `ControlPath=none` so no master connection is left behind, a hard timeout) plus a TCP connect
to the same port to tell a hung host from an absent one. The `ssh-check` check adds one more such attempt for each alias in `doctor.ssh_check` (and, with `from_ssh_config`, each plain `Host` name of the operator's ssh config): `BatchMode`, the no-op `echo`, a hard timeout, never a password, a host-key prompt, a ControlPath or a shell. It reads what the server printed to recognise an additional-check prompt and **never opens the URL it carries or completes the check** ([ssh-check.md](ssh-check.md)); it changes no ssh config, key, known-hosts file or network policy. A Windows host is asked one PowerShell script, sent as a single line of
base64 on standard input (a command line cannot hold it) and run in memory: it reads memory and process counters, the process
table with parent links (never printing a command line: only the flag *names*, which configured marker matched and a short hash),
a few registry values (the default shell, the per-app GPU preference, App Paths) and the file version of a few tools, and writes
nothing there. No program on the host is run to learn a version. The Infirmary never calls `wsl.exe` (a hung WSL hangs it too) and
never ends, starts or changes a process, service or file on a Windows host; the test fixtures refuse a probe script that holds a verb
that could.

**The Windows platform (catalogue 23 to 27).** One more read-only PowerShell script (`windows/platform.ps1`) answers these checks. For a
Windows host in `doctor.hosts` it is appended to the same probe (still one ssh call per host and tick); for this machine's own Windows
side it is sent the same way to the local `powershell.exe` (from a WSL guest through interop, or on Windows itself), the one program the
Infirmary starts for it. It reads CIM classes (`Win32_ComputerSystem`, `Win32_Processor`, `Win32_DeviceGuard`, `Win32_OptionalFeature`), the
count of registered WSL distributions per version, named Windows Update values in the registry, the two restart keys and three softer
signs, and the System event log of the last `lookback_h` hours, keeping from a restart event only the program's file name, the reason,
its code and the kind (no user or computer name). It needs no elevation. It never changes firmware, boot configuration, policy,
services or scheduled tasks, installs nothing, writes nothing and never calls `wsl.exe`; the fake `ssh` and `powershell` of the fixtures
refuse a script holding a verb that could (including `bcdedit`, `reg add`, `schtasks`, `dism`, `Set-`, `Install-`, `Invoke-`).

**Processes it starts.** Only through `core/execute.py`: argv arrays, no shell, stdin closed, output capped, a
timeout. The git calls are read verbs (`rev-parse`, `cat-file`, `merge-base`, `rev-list`, `config --get`, `config --show-scope --get-regexp`, `var GIT_AUTHOR_IDENT`, `status`, `symbolic-ref`, `log`) with
optional locks off. The incident rules' next steps (a relaunch, a steer, a re-arm, a git command)
are text for a person; the Infirmary runs none of them. An owner's dry run runs with `--dry-runs`, or as a repair's reading in `repair` mode, `plan` and
`approve`, from the argv the action's code builds. An owner's repair command runs only in a tier-1 run while live repair is on for the home, or
under `approve`. The one signal the Infirmary ever sends is the timeout ending its *own* child; it never signals any other
process. Owners stop processes through their own identity checks; the Infirmary first proves that what they list is what it
found (pid, start time and command line, unchanged between its two readings).

**Identity.** A process is the same process only when pid, start time and command line match what was
recorded; records without a start time are accepted only when the pid began no later than the record.
Nothing here matches processes by name, and the Infirmary signals no process.

**Guard-friendly.** One entry point per tool with fixed verbs (`check`, `plan`, `plans`, `approve`, `reset`, `status`,
`off`, `on`, `live`), no shell strings, no `eval`. The repairs are a closed set in code, run an owner's argv array, and are inert
plan files at the human tier; configuration can hold an action back but never add or widen one.

## Live repair: the operator's switch

Live repair is **one switch, per home, off by default**. Off means *advice-only*: the Infirmary reports findings and writes plans, and
never runs an owner's command, signals a process or changes a file outside its own state directory. On means *live-repair*: the
tier-1 repairs of [repairs.md](repairs.md) run on that home.

```
infirmary live            # or `live status`: the mode, who turned it on and when
infirmary live on         # the operator turns live repair on for this home (--by NAME to name who)
infirmary live off        # turns it off again
```

- **Who and when.** `live on` and `live off` write `<state dir>/live-repair.json` (`infirmary.live-repair 1.0`: `enabled`, `by`, `at`,
  and the scope: host, adapter and, where the adapter has one, the home) and a `live-repair` line in `actions.jsonl`. A record
  that is missing, unreadable, says off, or was written for another host, adapter or home is **off**: a state directory that is
  copied or shared never switches repairs on somewhere else.
- **Nothing turns it on but the command.** There is no configuration key, no environment variable, no default, no installer step
  (`infirmary install` is advice-only, and the run it schedules never repairs) and no adapter hook that enables it; `mode: repair`, `--set mode=repair` and `INFIRMARY_MODE=repair` are usage errors. The Infirmary
  itself never calls it. Whoever runs `infirmary live on` has decided it, and the log says who.
- **What changes when it is on.** `check` runs the tier-1 repairs of the adapter (each through its owner's command, after two
  readings, then verified) and writes the tier-2 plans, instead of only saying what it would do. Nothing else widens: the
  action set is the same closed set in code, and configuration still cannot add or widen an action.
- **What still protects.** Every gate is unchanged: the kill switch, the host circuit breaker (4 or more open findings, or a
  critical host), the per-action breaker (2 failed or unverified runs), flap detection (3 repairs of one finding in 24 h), the
  rate limits (3 per host, 2 per action, 1 per finding per hour), the Infirmary-wide lock, and the two readings with identical
  targets. The toggle is read again right before the second reading and right before the act, so `live off` stops a repair
  that is in flight, as the kill switch does. `check --no-write` acts on nothing even with live repair on.
- **The kill switch overrides it.** With the kill switch on (`infirmary off`, the `off` file, `INFIRMARY=off`) nothing is
  checked or repaired whatever the toggle says; `status` shows both: `live repair: ON ... the kill switch is on, so nothing acts`.
- **`approve` is separate.** `infirmary approve PLAN_ID` is a person's own act on one reviewed plan; it works with live repair
  off (advice-only) and is still refused under the kill switch.
- **How to turn it off.** `infirmary live off`. Or throw the kill switch. Deleting `live-repair.json` also returns to advice-only.
- **Where it shows.** `infirmary status` and `live` (text and `--json`: `repair_mode` is `advice-only` or `live-repair`, plus
  `live_repair` with `enabled`, `by`, `at`), the header of `check`, `doctor.json` (`repair_mode`, `live_repair`, and `repairs.repair_mode`)
  and the Lookout source label (`Infirmary · advice-only` or `· live-repair`) so a dashboard card can show it. The Infirmary works
  the same with no dashboard.
