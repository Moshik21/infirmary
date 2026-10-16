# Infirmary

Infirmary checks a fleet of machines and agent lanes for stalls and gives advice. It is advice-only, and repair is off by default; live repair has never run on a live fleet. MIT licensed, see [LICENSE](LICENSE).

*Two commands: `stall-explain` (explains a quiet lane) and `infirmary` (detects the snags a fleet of agent lanes runs into).*

When a fleet of long-running agent lanes goes quiet, most of the alarms are not stuck workers. They are
a lane that finished and waits for whoever supervises it, a long test that is queued behind another, a
declared wait, a model call that is thinking. `stall-explain` answers "why is this lane quiet?" with one
line and the evidence behind it, from local files and `/proc`, in well under a second:

```
$ stall-explain lane-finished
waiting-on-supervisor · lane-finished · finished 30m ago; the next move is the supervisor's
$ stall-explain lane-busy -v
running-long · lane-busy · agent process tree is burning 1.0 cores
  declarations: newest event: working 2h00m ago "started"
  process tree: 1.0 cores over the last sample, 0 descendants
  recommendation: no action
  took 411 ms (budget 1000 ms)
$ stall-explain lane-quiet --idle-s 3600; echo $?
wedged · lane-quiet · idle 60m, nothing running or declared
30
```

**What it is.** A deterministic explainer: a fixed rule order over evidence a source collected, one
verdict, an exit code per verdict, `--json` for machines, and a `--replay` mode that scores the rules
against recorded alarms before anyone relies on them.

**What it is not.** Not a monitor, not a restarter, not an alert router, no model calls. `stall-explain` only
explains. `infirmary check` detects and advises by default (**advice-only**); once an operator runs `infirmary live on`
for a home it runs a short, closed list of safe repairs on that home (**live-repair**), and only by calling the owner's own
command (below).

## Status and intent

**Status.** Version 0.4.0, the first public release. This is early software, written against one fleet's incidents and maintained by one person.

- `stall-explain` and `infirmary check` run on Linux and WSL2. macOS and Windows are partial: many rows there report `unknown` or are designed but not yet run, and a Mac was not measured. Do not run `scripts/test` on a Mac ([Platforms](#platforms)).
- It is advice-only by default. Live repair has never been run on a live fleet; it has only run against synthetic homes and shim owners, and it is off unless an operator turns it on.
- The 88.5% replay accuracy comes from one fleet's alarms, which the rules were written against. Read it as a description, not a forecast ([Measured accuracy](#measured-accuracy)).
- The hosted CI matrix across Linux, macOS and Windows is a manual workflow. Its first run (2026-10-16) passed the whole suite on Ubuntu (Python 3.8 and 3.13) and failed 3 checks each on macOS (3.13) and Windows (3.8 and 3.13); those are not fixed yet ([Platforms](#platforms)).
- It ships no adapter for a specific tool, only the example in `examples/adapter/files-home/`.

Issues are the useful feedback: a verdict that looks wrong, a check that reads `unknown` on your machine, a platform it fails on, or a first run that confused you ([SUPPORT.md](SUPPORT.md)).

**Intent.** Most alarms from long-running agent lanes are not stuck workers. Infirmary answers "why is this lane quiet?" with one line and the evidence behind it. It is deterministic, makes no model calls and reads local records. It advises, and it does not act unless an operator opts in. It is deliberately not a monitor, not a restarter and not an alert router.

## Five-minute quickstart

Needs Python 3.8 or newer. No install, no dependencies. Two words you will meet: a **lane** is one long-running agent session (one
worker, with its own state files and processes); an **adapter** is a small directory of code that teaches the Infirmary where one
supervising tool keeps its lanes, so it can read them, schedule the advise run and apply the incident rules for that tool.

Clone this repository (or unzip the package), `cd` into it, and run these from there. Every command is `bin/...`, so nothing has to be on
your `PATH` (to type plain `infirmary` instead, run `export PATH="$PWD/bin:$PATH"` once in that shell):

```
bin/stall-explain --demo                              # replays 15 invented alarms, every verdict, with the accuracy gate
python3 examples/demo-fleet/make_demo.py /tmp/demo    # five real little lanes (two child processes, gone after 60 s; add --stop DIR to end them now)
bin/stall-explain --set files.lanes=/tmp/demo/lanes.json lane-busy -v
bin/stall-explain --set files.lanes=/tmp/demo/lanes.json lane-quiet --idle-s 3600
bin/infirmary check                                   # the catalogue: disk, memory and swap, orphaned servers, ... on this machine
bin/infirmary check --json --no-write                 # the same as `doctor.json`, writing nothing
bin/infirmary status                                  # kill switch, host identity, the host beat, the last check, breakers, plans
bin/infirmary live                                    # is live repair on for this home? (off unless you turned it on)
bin/infirmary plans                                   # tier-2 repairs wait as plans until a person approves one
bin/infirmary advise --no-write                       # the advice-only pass: the check plus stall-explain over the day's recorded alarms
python3 scripts/test                                  # the fixture and smoke tests (repository only, not in the package; Linux/WSL2 only, see Platforms)
```

Not in the block above on purpose, because they change state or need something of yours: `bin/infirmary live on` and `live off` (the
operator's one switch for live repair; leave it off while you are looking around), `bin/infirmary approve PLAN_ID` (needs a plan from
`plans`), and `bin/infirmary install --home HOME --adapter NAME --advice-only --dry-run` (schedules the advise pass for one home with one crontab
line; `uninstall` undoes it; `HOME` must be a real directory your adapter knows). To see it work with nothing of your own, use the example adapter
over a folder that holds a `lanes.json` (the demo's does):

```
bin/infirmary install --home /tmp/demo --adapter files-home --advice-only --dry-run --set 'adapter_paths=["'$PWD'/examples/adapter"]'
```

**What works with nothing else.** `stall-explain` over your own `lanes.json` ([docs/sources.md](docs/sources.md)), `--demo`, `--replay`,
and `infirmary check`, the generic host checks (most rows say `skip` with the reason until you configure what they need).

**What needs an adapter for your own tool.** The scheduled advise run (`install`, `advise` over a home's recorded alarms) and the incident
rules read a supervising tool's own records, so they need an adapter that knows that tool. This repository ships no adapter for a specific tool:
`examples/adapter/files-home/` is a small generic one over plain files (a `lanes.json` and an `alarms.jsonl`) with one check, one
incident rule and one repair action, to run as it is or copy ([its README](examples/adapter/files-home/README.md)). To write your own, copy that folder,
replace the file reads in `source.py` (and the check, rule and action in `doctor.py`) with reads of your tool's records, keep it in a directory listed in
`adapter_paths` and select it with `--adapter NAME` ([docs/sources.md](docs/sources.md), [CONTRIBUTING.md](CONTRIBUTING.md#adding-an-adapter)); until you do, those parts do nothing for you. **Live repair has never been run on a live fleet by the project**; it has only run against synthetic homes and shim owners, and
it is off unless an operator turns it on ([docs/safety.md](docs/safety.md)).

**A first `infirmary check` can read CRITICAL, exit 30, on an ordinary machine.** The free-space defaults come from one fleet's incidents
(warn under 40 GB free, critical under 15 GB; 15% and 7% of the volume also apply), so a small disk or a RAM-backed `/tmp` or home is called
critical and sets the `drain` flag, which only records the word and stops nothing. Read the row; if it is the disk, set the thresholds for
your machine (`--set doctor.disk.warn_gb=2 --set doctor.disk.crit_gb=1`). See [docs/troubleshooting.md](docs/troubleshooting.md).

To point it at your own lanes, write a `lanes.json` ([docs/sources.md](docs/sources.md)) or use an
adapter. Settings live in `infirmary.json` ([docs/verdicts.md](docs/verdicts.md) lists every threshold).

## The Infirmary: `infirmary check`

Ten snags (and, for Windows hosts, three more: a hung WSL, leaked farm processes, capability drift; and for the Windows platform under WSL lanes, five more: virtualization off in firmware, hypervisor launch failures, a pending restart, Windows Update restarts, the update policy) that cost real hours on one fleet, each detected from files, `/proc` and read-only git, each reported as a
finding with its evidence, a plain explanation, the owning script's dry-run command as the "how", and a recommendation:

```
$ infirmary check
infirmary 0.4.0 · host box (wsl) · mode advice-only · 17 checks in 86 ms
[ok  ]  1  Remote job workers                 1 worker tree(s) for ~/.state/remote-job, code identity matches
[CRIT]  2  Other hosts (hung or out of memory) 1 finding(s); win-box: 884 MB free of 32189 MB, commit 88%
        - win-box: win-box is short of memory
          only 884 MB of 32189 MB free
          how: ... the Infirmary sets the drain flag and stops nothing.
[WARN]  4  Review-gate refs                   1 finding(s); 7 gated lane(s) compared, 6 in step with their gate
        - lane-7: the review gate holds work the lane does not
          gate 1111aaaa is not an ancestor of lane 2222bbbb and the lane is not an ancestor of the gate ...
          how: dry run (in /path/to/worktree): gate-tool sync --check; ...
...
CRITICAL · 1 critical, 1 warning · exit 30
```

It also writes `doctor.json` (findings, per-host `drain`/`hung`/`disk_critical` flags, its own cost) and, with `--push`,
publishes the findings as cards on a [Lookout](docs/findings.md#lookout-feed) board. The catalogue, its thresholds and
what each check needs: [docs/catalogue.md](docs/catalogue.md). `check --card DIR` or `infirmary card` also produce one small
status card for a dashboard that reads them ([docs/findings.md](docs/findings.md#status-card-optional)); nothing is written unless asked. What the Infirmary could not decide is never blank on any of these surfaces: each distinct unknown is one card with its exact reason, and the status card never says all clear beside one ([the unknown vocabulary](docs/findings.md#the-unknown-vocabulary)). Detection of items needing a specific tool lives in that
tool's adapter. One check is about the operator: `ssh-check` recognises an ssh login that stops at an additional-check prompt
(Tailscale SSH) and reports it with its login URL, because steers and pushes over that alias then wait silently until the operator
signs in ([docs/ssh-check.md](docs/ssh-check.md), with the check-period setting).

## Incident cards: one row for each failure that has happened

Some failures are not snags to list but things that have actually happened: a worker's agent died while its window stayed open, the
supervision watcher went down, a mirror stopped pushing, a machine stopped answering, a project clone has no git identity, a pipeline gate is
parked and nobody drives it. For each, the Infirmary writes **one append-only row** in plain words: what happened, since when, what it blocks,
whether work is at risk, the records it rests on with their ages, and the one safe next step as text (for a worker whose agent is gone: the
relaunch command that keeps the worktree). Rows go to `incidents.jsonl` in the state directory (never rotated, never edited; what is open now is
`incidents.json`), appear under their findings in `check`, first in `advice.txt`, in the status card and, with `--push`, on the board. An input a
rule cannot read is an **unknown row** that says what could not be read, never silence; a healthy look-alike stays quiet. Advice-only: nothing
acts on a row. A gone agent is carded after 3 minutes of silence; `install --every 2` makes it a row within 3 to 5
minutes. The six rules, their look-alikes, the row format and the cost are in [docs/incidents.md](docs/incidents.md).

## Repairs: `live on`, `plan`, `approve`

The Infirmary never signals a process, deletes a file, moves a branch or edits a record itself. A repair calls the **owner's
own command** with the arguments the owner's dry run proposed:

- **Tier 1** (run unattended while live repair is on for the home) only where the owner itself proves its target safe and nothing touches a
  lane, a session or a pool slot, e.g. "stop job workers whose code root is gone" through the owner's reap script. Each run
  passes the gates (kill switch, a host circuit breaker when 4 or more findings are open or the host is critical, a per-action
  breaker after 2 unverified runs, flap detection, rate limits of 3 per host, 2 per action and 1 per finding per hour, one
  repair at a time), takes two readings of the owner's dry run and of the finding (the second immediately before acting,
  with the same targets required), acts once, and counts as done only when the check no longer finds the snag.
- **Tier 2** (everything riskier) is an inert plan file. `infirmary approve PLAN_ID` re-verifies it from scratch and runs
  exactly its argv once; a hand-edited, expired, used or outdated plan is refused. Plans an owner must carry out (a lane's
  worker moving its own branch) are only handed over.

**Live repair is one explicit switch, per home, off by default.** Until an operator runs `infirmary live on` the Infirmary is
*advice-only*: findings, explanations and plans, and it never acts on the home. `live on` records who turned it on and when
in the home's own state directory (`live-repair.json`); `infirmary live off` turns it off again, at once, even for a
repair in flight; `infirmary live` and `infirmary status` say which mode it is in (`advice-only` or `live-repair`, also
in `doctor.json` and on the Lookout source label). Nothing turns it on for you: no configuration key, environment variable,
default, installer or adapter does, and `mode: repair` is refused as a setting. With it on, every gate still applies
unchanged, and **the kill switch wins over it** (`infirmary off`, `INFIRMARY=off`). `infirmary approve` is a person's
own act and works in either mode. See [docs/safety.md](docs/safety.md#live-repair-the-operators-switch).

Every run and refusal is a line in `actions.jsonl` with its reason and both readings, and a Lookout card. Details, the
gates and their numbers, and how to add an action: [docs/repairs.md](docs/repairs.md). The example adapter ships one action
(`examples/adapter/files-home/`); an adapter you write adds its own.

## Advice on a schedule: `install`, `advise`

`infirmary install --home HOME --adapter NAME --advice-only` schedules `infirmary advise` for one home every 10 minutes. It
adds one line to the user's crontab (one LaunchAgent on macOS) and nothing system-wide, restarts nothing and edits no shared
configuration. `infirmary uninstall --home HOME` takes it out again and leaves the crontab as it was. `infirmary installed
--home HOME` says whether it is in place and its advice fresh. Each run does the catalogue check, never repairing, and explains
every alarm the home recorded in the last 24 h. It leaves `advice.txt` and `advice.json` in the home for a supervisor to read at
wake time, plus a `lookout.card` 1.0 card: how many alarms were explained, by which explanation, and how many were real. The
install has no repair option, and the scheduled run repairs nothing even if live repair is turned on later. Formats, the card and
the install: [docs/advice.md](docs/advice.md).

**Judged alarms.** For every alarm the advise run explains it appends one row to `judged.jsonl` in the state directory (append-only, never edited):
the verdict it gave, the sources it rests on and how old each state source was. Once 30 minutes have passed, a deterministic follow-up appends a
new row naming it: what the lane did next (relaunched, gone, finished, answered, moved, stuck, or quiet) from the records the run already read,
and whether the verdict was wrong, by a fixed table: a benign verdict followed by a stall that persisted or by a relaunch is **wrong-benign**; a real
verdict where the lane moved on by itself with no relaunch and no instruction recorded is **wrong-real**; anything not yet decidable or lacking
evidence is **undecided**, with its reason, and never counted as agreed. `infirmary judged` prints the counts for a window (explained, real,
wrong-benign, wrong-real, undecided). Nothing acts on a row. The table, what the records cannot know and the format: [docs/judged.md](docs/judged.md).

## The verdicts

| Verdict | Exit | Meaning |
| --- | ---: | --- |
| `queued`, `running-long`, `running-remote`, `thinking`, `declared-wait`, `torn-down` | 0 | explained; nothing to do |
| `waiting-on-supervisor` | 10 | the worker is done or asked a question; the supervisor's move |
| `waiting-on-human` | 20 | a prompt needs a person |
| `wedged` | 30 | alive, idle, nothing behind it |
| `dead` | 40 | nothing there |
| `unknown` | 50 | the evidence does not decide; never a guess |

A benign verdict needs positive evidence that was seen (a live job, a queue ticket, process or file
activity, a declaration). Silence alone is `unknown`. Details and thresholds: [docs/verdicts.md](docs/verdicts.md).

## Measured accuracy

Replayed over 54 real stale alarms from one fleet's day, against a hand classification: both real stalls
flagged, 46 of the other 52 benign or supervisor-owed (88.5%; gate 80%). The rules were written against
this same data, so treat it as a description, not a forecast. Full table, the six misses and the
method: [docs/accuracy.md](docs/accuracy.md).

## Cost

One verdict takes about 5 ms from files and 0.4 s when it also samples a process tree; RSS about 27 MB;
the 63-alarm replay runs in about 50 ms. The hard cap is 2 s, after which the answer is `unknown`. The incident rules run inside the Infirmary's existing 2 s local budget; the fold into rows and the ledger append are
`cost.incidents_ms` in `doctor.json`, under 1 ms; a row is about 1.7 KB. The judged ledger adds no process: its rows are appended from what the advise run already read, 1 to 2 ms
a run on the fixtures' synthetic home (21 lanes, 105 alarms; the whole advise run about 20 ms), a verdict row about 0.7 KB and an outcome row about
0.4 KB (100 alarms a day is about 110 KB a day), the ledger read once per run at about 11 ms for each MiB; the one extra read is the names and times
of the files in a lane's own inbox ([docs/judged.md](docs/judged.md)).

The advise run keeps its own cost down: a wall-time budget (30 s), a CPU budget (30 CPU-seconds an hour) and a back-off when the machine's 5-minute load per
core is above 0.8 or it is on battery below 30%, in which case it says `skipped on purpose: load 0.90 per core (limit 0.80)` instead of running ([docs/advice.md](docs/advice.md#what-a-run-costs-and-when-it-steps-aside)).
Measured on a Linux/WSL2 machine (24 cores): 2.2 s wall, 0.28 CPU-seconds and 35 MB for one run over a real home with 13 alarms, 0.02 s and 0.01 to 0.02 CPU-seconds
over the fixtures' synthetic home; a Mac was not measured.

## Platforms

| | Linux / WSL2 | macOS (Apple Silicon) | Windows 11 |
| --- | --- | --- | --- |
| Verdicts from files, `--replay`, `--demo`, `--json` | **run** | designed, not yet run | designed, not yet run |
| Process tree, CPU, process identity | **run** (`/proc`) | reported "unavailable", not guessed | reported "unavailable", not guessed |
| Infirmary: disk, memory and swap, orphaned servers | **run** | parsers run against recorded or documented `ps`/`vm_stat`/`sysctl` output; **not run on a Mac** | reports `unknown` for process and memory checks, never "fine" |
| Infirmary: scratch and RAM-backed volumes, WSL interop (catalogue 19 and 20) | **run** on Linux/WSL2 (fixtures and read-only on a live fleet host) | scratch by command lines only; what needs `/proc` says `skip` or `unknown`; **not run on a Mac** | reports `unknown` or `skip`, never "fine" |
| Infirmary: the Windows platform under the lanes: firmware virtualization, hypervisor launch failures, pending restart, Windows Update restarts, update policy (catalogue 23 to 27) | **run** from a WSL2 guest through `powershell.exe` (fixtures, and read-only on a live fleet host); the same script rides on the ssh probe of a Windows host (fixtures) | skip (no Windows side) | same code natively, **not run on Windows** |
| Infirmary: ssh additional-check prompts on each configured alias (catalogue 28) | **run** (fixtures, and read-only on this machine's aliases) | same code, not run on a Mac | reports `unknown`, never "fine" |
| Infirmary: probing another host (ssh), including a Windows host's memory, its WSL VM, leaked farm processes and capabilities | **run** (Linux/WSL to a Windows host over ssh, read-only) | same code, not run | same code from Windows, not run |
| `advise`, and the per-home install (`install`, `installed`, `uninstall`) | **run**: `advise` on synthetic homes and read-only (`--no-write`) on a live home; the install against a `crontab` shim | the LaunchAgent install is run against a `launchctl` shim on Linux only; **not run on a Mac** | no scheduler entry; `advise` runs by hand |
| Infirmary: repairs, plans, `approve`, breakers, kill switch, the live-repair toggle | **run** against synthetic homes and shim owners only; the toggle is off by default and no repair has been run on a live fleet by the project | same code; a repair that stops processes is always a plan there (no pidfd); not run | lock via `msvcrt`; an owner is a POSIX script in the example adapter; not run |

"Run" means exercised by `scripts/test` (or, for a probe of another machine, run against the real machine) on that
platform during development (Linux/WSL2 with Python 3.14). `scripts/test`
here runs the core, the generic checks and the example adapter. `scripts/test` passes on Python 3.8, 3.11 and 3.14; 3.9, 3.12 and 3.13 have not been run. `scripts/test` is the only suite, and it is
not a three-OS smoke: it has been run to a pass on Linux and WSL2 only. **Do not run it on a Mac.** It starts the example adapter's scheduler fixtures, and on
macOS the default scheduler is launchd, so a run there wrote real LaunchAgent plists into your `~/Library/LaunchAgents` (the install scenario now asks for cron
explicitly, which has not been confirmed on a Mac), and a run on a Mac failed 13 of its checks. On Windows the scenarios that use POSIX shims are skipped by design, and the suite has not
been run there. `.github/workflows/ci.yml` runs the suite on all three systems, by hand only
(`workflow_dispatch`), so the matrix is a way to find out, not a record of a pass. The first hosted run (2026-10-16): Ubuntu with Python 3.8 and 3.13 passed every check; macOS with Python 3.13 failed 3 (one scheduler-install scenario, `lane-gone` exits 40, and the one-hour-silence `wedged` exit); Windows with Python 3.8 and 3.13 failed 3 each (a self-audit denylist check, `lane-gone` exits 40, and the `wedged` exit). They have not been investigated, so do not read them as proven defects or as proven fine. The macOS 3.8 cell is not in the matrix.

## Honesty

The dashboard repository's `docs/honesty.md` sets the rules for everything that feeds a board; this section is what they mean here.

- **What each output is.** A verdict is **derived**: a fixed rule order over evidence a source **measured** (files, `/proc`, read-only git), and the first rule with positive evidence wins ([docs/verdicts.md](docs/verdicts.md), `src/infirmary/explain/engine.py`). A finding is **measured** facts plus a plain explanation, the evidence it rests on listed beside it ([docs/findings.md](docs/findings.md)). An incident row is **derived** from the records it names, with their ages ([docs/incidents.md](docs/incidents.md)). The accuracy figure is a **measured** replay of one fleet's alarms, and says it was not held out ([docs/accuracy.md](docs/accuracy.md)).
- **What it cannot know.** Only what its sources recorded. Silence alone proves nothing, so a lane with no positive evidence is `unknown`, not benign ([docs/verdicts.md](docs/verdicts.md)). It cannot see inside an agent's model call, work no record shows, or a Mac or Windows host it has not run on ([Platforms](#platforms)).
- **Unknown and stale are shown as such.** The `unknown` verdict (exit 50) is never a guess; a rule that cannot read its input writes an `unknown` incident row naming what it could not read, and a record with no time reads "age unknown" ([docs/incidents.md](docs/incidents.md), `src/infirmary/explain/engine.py`). A declaration older than the last boot or the lane's session does not count ([docs/evidence.md](docs/evidence.md)). Nothing is called fine because nothing was seen.
- **What it never claims.** A cause it was not given, an accuracy beyond the replay above, or a number about its own value: the judged ledger counts what a verdict was followed by, and calls what it cannot decide `undecided` ([docs/judged.md](docs/judged.md)). It makes no model call ([What it will never do](#what-it-will-never-do)).
- **Backing off is said, never silent.** An advise run that steps aside because the machine is loaded, on a low battery or past its CPU budget writes `skipped on purpose: <reason>` as the first line of `advice.txt` and as the card's headline (status `unknown`, never `ok`); a reading it could not take (load, power) is "not read", never zero ([docs/advice.md](docs/advice.md#what-a-run-costs-and-when-it-steps-aside)). Version gaps are the dashboard's own words, never the Infirmary's comparison: a machine whose version the dashboard cannot read is "unknown", not "old" ([docs/catalogue.md](docs/catalogue.md), item 35).
- **Advice-only takes no action.** Until an operator runs `infirmary live on` for a home, `check` and `advise` only detect, explain and advise; nothing turns live repair on for you, and the scheduled `advise` run never repairs ([docs/safety.md](docs/safety.md), `src/infirmary/core/live.py`). Live repair, when on, calls only an owner's own command, and every run and refusal is a line in `actions.jsonl` ([docs/repairs.md](docs/repairs.md)).

## What it will never do

- Act while live repair is off (the default, advice-only): no signal, restart, file change or command outside its own state directory.
- Turn live repair on by itself. Only the operator's `infirmary live on` does, per home; no setting, default, installer or adapter can.
- Act, even with live repair on, except through an owner's own command, from a closed list in code, after re-checking right before acting;
  signal a process itself; force anything; delete what an owner's dry run did not list; discard unlanded work; let
  configuration add or widen a repair.
- Open a network connection except a Lookout push you configure, with the token read from a file or an
  environment variable, never from a command line, and the read-only probes of the hosts you list.
- Change a firmware, boot, update-policy, service or scheduled-task setting on any machine, or install anything; on Windows it only reads.
- Read or print a secret, send telemetry, or call a model.
- Call anything "fine" because it saw nothing. A rule that cannot read its input writes an **unknown** incident row naming what it could not
  read; a declaration that predates the last boot or
  the lane's session counts for nothing; a record with no time is shown as "age unknown", never as fresh.
- Rewrite, trim or delete an incident row, or act on one: the ledger is append-only, and the next step in a row is text for a person.

See [docs/safety.md](docs/safety.md). Layout: `src/infirmary/` (core, explain, doctor), `examples/` (with `examples/adapter/files-home/`, an adapter to copy; adapters for a specific tool
live outside this repository, in a directory named by `adapter_paths`), `docs/`, `scripts/` (`self-audit`,
`build-package`, `test`, `merged-check`). Licence: MIT ([LICENSE](LICENSE)). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).
Security: [SECURITY.md](SECURITY.md). Support: [SUPPORT.md](SUPPORT.md). Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Changes: [CHANGELOG.md](CHANGELOG.md).
