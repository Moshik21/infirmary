# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/). Schema versions (`infirmary.verdict`, `infirmary.evidence`,
`infirmary.replay`) are versioned separately from the tool.

## [0.4.2] - 2026-10-16

- **The advise job's default CPU budget is 40 CPU-seconds an hour, not 30.** An install that runs `advise` every 120 s on a Mac measured 1.6 CPU-seconds
  for a full run (95th percentile 2.5; 0.28 on Linux), so 30 allowed about 19 full runs of the 30 an hour, as few as 14, and 179 of 651 runs in 22 hours
  stepped aside on `cpu budget used` (another 102 on load, which is unchanged). At 40 it is about 25 an hour and in the resampling never fewer than 21, using at most about 1.2%
  of one core. The budget still stops a runaway: it caps the hour at about four fifths of what running every cycle in full would cost. An install that sets
  `advice.budget.cpu_s_per_hour` itself is not affected. The accounting was checked and does not over-count (each child once, a skipped run's 0.01 to 0.03
  CPU-s included). Measurements and method: [docs/advice.md](docs/advice.md#what-a-run-costs-measured).

## [0.4.1] - 2026-10-16

- **macOS and Windows read their own process table.** `dead` and `wedged` need the agent's process identity and CPU, which came only from
  `/proc`, so on macOS and Windows a lane that was gone or silent read `unknown` (exit 50, "process unavailable"). macOS now reads `ps`
  (`TZ=UTC LC_ALL=C`, start time and CPU from the whole table, the one command line by pid) and Windows reads `Win32_Process` through one
  fixed, read-only PowerShell script. A reader that is missing, times out or prints anything it cannot parse whole still gives `unknown`,
  and the verdict now says why ("process unavailable: ps timed out after 3s"). Off Linux the identity, the CPU sample and the tree come
  from one read; on Windows that read starts PowerShell (about 1.3 s with the CPU sample on a Windows 11 PC), so a verdict there is over
  the 1 s target and on a slower machine can reach the 2 s cap and read `unknown`. Linux and WSL2 read `/proc` exactly as before.
  Run by `scripts/test` on Linux/WSL2 and on a Windows 11 PC, and the Windows reader against live processes; the macOS reader was run
  read-only on a Mac and its parser against synthetic output, not by `scripts/test` on a Mac. The hosted matrix has not run this release.

## [0.4.0] - 2026-10-16

The first public release. It folds in everything recorded since 0.3.0 (below), and it is a breaking release: the rename from Fleet Doctor to Infirmary
(commands, package, environment variables, state and config paths, schema ids; see `MIGRATION.md` in the repository) and the removal of the bundled adapter.

- **Removed: the bundled adapter.** `adapters/` (the adapter for the author's own supervisor, with its fixtures, recorded replay files and results notes) is no
  longer in this repository, and the core names no tool. Adapters now live outside the core, in any directory listed in `adapter_paths`; the only adapter
  shipped here is the generic example, `examples/adapter/files-home/`, and the docs point to it for how to write one. An install that still names the removed
  adapter and has no copy of it on `adapter_paths` shows an `adapter` error row (exit 50) and `advise` says the incidents were not read: that is not a pass.
  The checks and incident rules that adapter carried (catalogue rows marked `(adapter)`) do not run without it, `scripts/test` no longer replays the recorded
  real alarm sets, and the numbers in `docs/accuracy.md` and `docs/judged.md` for them can no longer be reproduced from a clone. `scripts/self-audit` no
  longer exempts a directory named `adapters` from the name checks (`--exempt` still takes any directory).
- **Fixed: output on Windows when stdout is piped or redirected.** `infirmary advise` ended with `UnicodeEncodeError` (exit 3) as soon as a line held a
  non-ASCII character (the `·` of the advice header), because a redirected stream takes the legacy code page. `infirmary` and `stall-explain` now write a
  redirected stream as UTF-8, and a terminal keeps its encoding and replaces what it cannot show (`src/infirmary/core/streams.py`). Reproduced with
  `PYTHONIOENCODING=ascii` on macOS (a pipe with a narrow code page); not run on Windows itself.
- **Corrected: what the README says about testing.** `scripts/test` is not a three-OS smoke: it has been run to a pass on Linux and WSL2 only, it fails
  13 checks on a Mac, and on a Mac the example adapter's install scenario used the default scheduler (launchd) and wrote a real plist into the user's
  `~/Library/LaunchAgents`. The README and CONTRIBUTING now say so and say not to run it on macOS; the scenario asks for `--scheduler cron` explicitly
  (confirmed on Linux, where that is already the default; not confirmed on a Mac).
- **CI is a manual workflow.** `.github/workflows/ci.yml` (it was kept disabled as `ci.yml.disabled`) runs the suite on Linux, macOS and Windows only
  when started by hand (`workflow_dispatch`).
- **`pyproject.toml`** gains `[project.urls]` (Source, Issues).

Open-source preparation: the placeholders are out. MIT licensed (see LICENSE); real CONTRIBUTING, SECURITY (GitHub private vulnerability reporting), SUPPORT (GitHub issues) and CODE_OF_CONDUCT (Contributor Covenant 2.1) text; documentation only, no change to any command's behaviour.

The self-audit takes any number of private name lists. Tooling and documentation only; no change to any command's behaviour.

- **`scripts/self-audit --denylist` can be repeated**, and so can the `--denylist` of `scripts/test`, `scripts/merged-check` and `scripts/build-package`, which hand every file to the audit (`$MERGED_CHECK_DENYLIST` may name several, separated by the platform path separator). The built-in list keeps only generic private-network patterns (reported as `private network name`); the tool and vendor names it held now live in a second private file kept beside the denylist, described in CONTRIBUTING. The audit no longer skips its own file.

A generic example adapter, and the core's install, advice-budget and repair-gate scenarios now run against it. No behaviour change in the product.

- **`examples/adapter/files-home/`**: an adapter over plain files (`lanes.json`, `alarms.jsonl`, declaration logs), with one check, one incident rule and one repair action that runs the home's own owner script. A stranger installs it with `infirmary install --home HOME --adapter files-home --advice-only --set 'adapter_paths=["<checkout>/examples/adapter"]'`.
- **24 scenarios ported** from the reference adapter's fixtures (`files-advice-*`: install on cron and launchd, refusals, the run's budget and back-off, the battery cases, the judged ledger over a rotated log; `files-repair-*`: the kill switch, the live-repair toggle, the circuit breakers, the rate limit and `reset`, `plan` and `approve`). `scripts/test` also runs every `examples/adapter/*/fixtures/*_fixture.py`, so the core's install and repair code is tested without the reference adapter present.

A merged-result check. Otherwise tooling and documentation; the one change to a command's behaviour is the adapter bullet below.

- **`scripts/merged-check`** runs the self-audit and the full suite on a head merged onto the base (default `origin/main`) in a throwaway scratch repository outside the working tree, so two changes that each pass alone but fail together are caught before the merge. One verdict line (`merged-check: ok` or `merged-check: FAILED <step>: <first finding>`), a non-zero exit on any failure, and the scratch tree is always removed. It is now in the definition of done in CONTRIBUTING and in the pull request checklist; `scripts/test` exercises it on a scratch git repository.
- **A named adapter that cannot be loaded is an error row, not a smaller green run.** With `adapter` set to a name found in no searched directory (or one that fails to load), `check` used to drop the adapter's checks and report OK, exit 0. It now has an `error` row named `adapter` that lists the directories searched, and exits 50; `advise` says `incidents: not read: adapter X not found`. `install` stores `adapter_paths` entries as absolute paths, because the scheduler runs from another working directory.

First-run fixes to the README, and the zip no longer carries `MIGRATION.md`. Documentation and packaging only; no behaviour change.

- **The quickstart can be pasted.** Every command is `bin/...` (with a one-line `PATH` step for plain `infirmary`), the clone line is prose, and the lines that change state (`live on`, `live off`, `approve PLAN_ID`, `install`) are described under the block instead of inside it. "Lane" and "adapter" are defined in two sentences; the README now says that the scheduled advise run and the incident rules need an adapter for your own tool (the reference adapter targets the author's), and that live repair has never run on a live fleet.
- **A first `check` can read CRITICAL.** Explained in the README and in [docs/troubleshooting.md](docs/troubleshooting.md): the free-space defaults come from one fleet, so a small or RAM-backed volume is called critical (exit 30, `drain` flag); how to set thresholds that fit.
- **Stale README text.** The sample `check` header now reads `infirmary 0.3.0 ... 17 checks`, and the Python sentence says `scripts/test` passes on 3.8, 3.11 and 3.14.
- **Issue templates** for a general bug and a feature request beside "Wrong verdict".
- **`scripts/build-package` leaves `MIGRATION.md` out of the zip** (it is a guide for installs of the old names); the one CHANGELOG link to it is now plain text so the zip has no broken link.

Renamed: Fleet Doctor is now **Infirmary**. See `MIGRATION.md` in the repository (not in the zip) for the full old-to-new list. No forwarding shim: the
command `fleet-doctor` is now `infirmary`, the package `fleetdoc` is now `infirmary`, `FLEET_DOCTOR=off` is now `INFIRMARY=off` and the
other `FLEETDOC_*` variables are now `INFIRMARY_*`, `fleetdoc.json` is now `infirmary.json`, the state and config paths (`fleetdoc`,
`fleet-doctor`) are now `infirmary`, and the schema ids `fleetdoc.*` are now `infirmary.*`. Entries below keep the old names as history.

The wheel is honest and two small command-line gaps are closed.

- **The wheel carries what the commands read.** `stall-explain --demo` failed from an installed wheel (the demo replay was read from `examples/`, which
  a wheel does not hold) and `infirmary check` showed checks 23 to 27 as `[ERR ]` with `FileNotFoundError` on Windows-side hosts and WSL (the two
  PowerShell probes `windows/platform.ps1` and `windows/probe.ps1` were not packaged). The demo replay now lives in the package
  (`src/infirmary/explain/synthetic.jsonl`, moved from `examples/replay/`) and both it and the probes are package data. CONTRIBUTING's wheel check now runs
  `stall-explain --demo` and `infirmary check --only 23` from the installed wheel in a fresh virtualenv, which `--help` and `--version` could not catch,
  and lists the two probes from the installed package (check 23 only opens them where there is a Windows side). Not yet in the wheel: `infirmary install`
  schedules the checkout's `bin/infirmary`, which the wheel does not hold, so scheduling the advise run still needs a checkout.
- **`pyproject.toml` uses the SPDX licence string** (`license = "MIT"`) instead of the deprecated `{ text = ... }` table, which setuptools stops accepting on
  2027-02-18. That needs `setuptools>=77` to build, and setuptools 77 needs Python 3.9 to run: a wheel is built on 3.9 or newer; the installed package still
  runs on 3.8.
- **`infirmary --version`** prints `infirmary VERSION`, as `stall-explain --version` does (it was a usage error; `infirmary version` still works).
- **`make_demo.py --help`** prints usage through argparse. It used to take `--help` as the output directory, make a directory with that name and start two child processes.
- **Proof** (`scripts/test`; no other new tests): `infirmary --version`, and `make_demo.py --help` run from a scratch directory leaves it empty.

The advise run costs the fleet as little as it can, and the Infirmary reads the dashboard's per-machine versions. Advice-only still.

- **A budget and a back-off for the advise run** ([docs/advice.md](docs/advice.md#what-a-run-costs-and-when-it-steps-aside)). `advice.budget` (all configurable): a wall-time budget (`wall_s` 30; the explanations stop when it is used up and the run says how many alarms it left, never counting them explained), a CPU budget (`cpu_s_per_hour` 30, measured with `os.times()` and kept in `advice-cost.json` in the state directory), and a back-off: the 5-minute load per core above `load_per_core_max` (0.8), or a battery below `battery_min_pct` (30) and not on mains, and the run does not start; on battery above that charge the wall budget is cut to `battery_wall_s` (10). A run that steps aside writes `skipped on purpose: load 0.90 per core (limit 0.80)` as the first line of `advice.txt`, as the card's headline (status `unknown`) and on the actions log, never silent and never a pass; the next scheduled run measures again and says `back after N skipped run(s)`. A reading that cannot be taken (load, power) is "not read", never zero. `advice.json` carries a `budget` block with the readings, the decision and what the run measured. `scripts/test` gained a `write_file` setup step and an `http` fixture server.
- **Measured** on one Linux/WSL2 machine (24 cores, 24 GB) with the cost block's own method: a real home with 13 alarms took 2.2 to 2.3 s, 0.28 CPU-seconds and 35 MB; the fixtures' synthetic home 0.02 s and 0.01 to 0.02 CPU-seconds. A Mac was not measured.
- **`dashboard-versions` leaves a placeholder alone.** A source the dashboard marks `placeholder: true` (it never reports on its own; another machine relays its work) that never reported (no `received_at`) and carries no adapter version used to be a permanent `version-unknown` row ("the version of its reports is not known"), though there is no feed to fail. It is now skipped: no row, and counted in neither the unknown nor the flagged total (the summary says how many were not judged). A placeholder that has reported or carries a producer version is judged like any source (one that reported with no version is still `version-unknown`), a source that never reported without the flag is still unknown, and a view with no source that reports (none at all, or only placeholders) is still `not examined`. Proof: the existing `scripts/test` version scenarios gained a placeholder with no version, one that reported with no version, one with an unreadable version and with a flag, `placeholder: false`, and a view of placeholders only; no new test file.
- **`dashboard-versions` reads the live view.** The dashboard's `/api/view` wraps the view (`{"view": {"sources": [...], "counts": ...}, "server": {...}}`); the rule looked for `sources[]` at the top level and so said `not examined: the view has no sources[]` against every real dashboard. It now reads `view` when that holds `sources`, else the document itself; a view that cannot be read or lists no source is still `not examined`. Proof: the existing `scripts/test` fixture scenarios gained the wrapped shape (found, and two unreadable cases).
- **The Infirmary reads versions: catalogue 35 `dashboard-versions`** (reference adapter only; [docs/catalogue.md](docs/catalogue.md)). From the dashboard's `/api/view` (`adapter_config.dashboard_view`: an http(s) address or a saved copy), `sources[].producer` and `skew[]`: a machine the dashboard flags (an older adapter than a change another machine already has) is one `version-skew` row with the dashboard's own reason and the one safe next step as text (update that machine's pusher); the flag is read before the version, so a prerelease, unreadable or absent version never hides it; an unflagged machine whose version is missing or has no leading dotted number is one `version-unknown` row (a prerelease or build tag is read by its core). The Infirmary never compares versions itself, a different version with no flag is quiet, and a view it cannot read is `not examined: <reason>`.
- **Proof** (`scripts/test`; no other new tests): load over and under the limit and back, a low and a charged battery, mains power and no battery, a reading that cannot be taken, the wall budget cut and an hour of CPU spent and forgiven; a flagged machine, an unknown version, a source that never reported, a different version with no flag, a stale source, a served view, and each way a view cannot be read. One-line mutations of the product, each caught (the reference adapter's results notes).

Community files and packaging metadata.

- **Community files.** `SUPPORT.md` is new; `SECURITY.md` and `SUPPORT.md` carry one placeholder each for the maintainer's contact
  (`SECURITY CONTACT` and `SUPPORT CONTACT`, to be set before publication); the README, CONTRIBUTING and the pull request template link
  the community files, and `scripts/build-package` ships `SUPPORT.md`.
- **`pyproject.toml`** (metadata only, no dependencies, Python 3.8+, MIT, a console entry for `infirmary` and `stall-explain`; the version
  is read from `VERSION` in `src/infirmary/__init__.py`). The zip and a wheel or sdist are both supported builds; see CONTRIBUTING.md.

Unknown is never blank on an Infirmary surface. Advice-only still.

- **Every unknown is a card with its exact reason** ([docs/findings.md](docs/findings.md#the-unknown-vocabulary)). A check that answered `unknown`, broke, or overran its remote budget was only a collector row on the board, and the status card's headline said
  `all clear` beside it. `doctor.json` (schema 1.7) now lists `unknowns[]`, one entry per distinct unknown: `could-not-read`, `check-broke`, `overran`,
  and `rule-unknown` (a finding that is itself UNKNOWN), each with its reason, its next step and a stable id (`unknown:<check>:<class>`). The status card says `no findings, 1 unknown: <check> · <class> · <reason>` (never `all clear`), has an
  `unknown` number, and the Lookout feed sends one `info` card per unknown (the same id every tick, and no second card for a rule's `incident-unreadable` finding); `check` prints an `unknown (N; ...)` block and the count in its footer;
  `advice.txt` lists each as `[UNKNOWN, not a pass]` and the alarm card says `doctor: N unknown, first ...` with an `unknown checks` number, and is `unknown` rather than `ok` while one stands. A skipped check carries its reason in the
  feed's collector `warnings`, and an over-cap feed says how many cards it left out (it counted only findings before).
- **No `display.tour` option exists in this repository**, so none was added; the status card is a still tile, and nothing here pages or rotates.
- **Proof** (`scripts/test`; no other new tests): a fixture for each reason class (an unreadable input, a check that breaks, a probe that overruns its budget, an unknown beside a finding, a finding that is itself UNKNOWN, the
  scheduled advice run) and the healthy look-alike that differs by one fact (the reading exists: no unknown card, `all clear`, and a skipped check says why). Each pins one card per id, the first across two runs.

Three leftovers of the Doctor's real runs on two machines. Advice-only still.

- **No lane the lane map names is silent** ([docs/incidents.md](docs/incidents.md)). On one machine a lane stopped on purpose by a declared `paused` line, like five others that were listed, was neither an incident nor an
  info row. The exact real shape could not be reproduced from the repository alone, so the rule now guarantees the general property: every lane record is judged, or listed as `info` with `not examined: <reason>` and its newest
  status line (the fleet ledger says it was cleaned up, it runs on another machine, its record names no worktree, its worktree is not a directory on this machine); a live lane with no agent whose logs still grow is
  listed too, saying its work goes on without it. A home with no live lane lists its lanes as well.
- **The gate-driver rule's unknown row names its input.** It said only that the onset was not recorded. It now names the exact file and reason (`state/<lane>.busy-state` missing, with no `ts=`, written before the last
  boot or the lane's newest session start, or in another state; the run ledger missing, unopenable or in another shape), and its card is dated by the oldest parked gate the run ledger records; an onset that nothing
  records says where it would be recorded.
- **The knowledge-mirror rule finds its sync log, and judges from git without one.** The log is looked for in order: the configured key, the `StandardOutPath` of a launchd job (`adapter_config.launch_agents_dir`) whose command
  names the knowledge-sync script (the plist file is read; no `launchctl` call), the crontab line's redirect, then `<clone>-sync.log`. With no log found it judges from the clone's git state (commits ahead of upstream, the last
  commit's age against `adapter_config.mirror_commit_stale_s`, 3 days; detached or mid-operation as before) and is unknown only when the clone cannot be read.

Work-at-risk across remotes. A lane whose commits were pushed to a remote named `fork` was reported as holding "3 commit(s) on no remote". The probe already counted every remote;
the cause was that no remote-tracking ref in the clone contained HEAD (a push by URL, a remote with no fetch refspec, or a stale tracking ref), and the answer took the absence of a
tracking ref as proof the commits were nowhere else. Advice-only still.

- **Commits no remote-tracking ref holds are unknown, not at risk** ([docs/catalogue.md](docs/catalogue.md), item 29). The probe asks `git for-each-ref refs/remotes --contains HEAD`
  (one call); a holder that is not `origin` is named in the row detail. The lane record's `pr_head=` equal to HEAD is proof the work is published. Where no tracking ref holds HEAD and
  `pr_head=` does not prove it, the answer for the commits is unknown: "no remote-tracking ref contains these commits; the tracking refs may be stale", with the newest tracking ref's
  age read from the mtimes of the `refs/remotes` files and `packed-refs` (no network call). Uncommitted files are still at risk. Commits held only by the review pipeline's own remote are
  stated plainly (a copy the pipeline keeps, not a place the work is published) and answer unknown; that remote's refs are left out of the commit count. Unreadable git is unknown
  with the reason. Fixtures: fork-only, no tracking ref, a stale tracking ref, uncommitted files with no tracking ref, pipeline-only, and `pr_head=` equal to HEAD with no tracking ref.

Declared waits: a correction to the agent-gone rule, found on a second machine's real data. Advice-only still.

- **A `paused` line is a declared wait, whatever its prose** ([docs/incidents.md](docs/incidents.md)). The rule flagged four lanes as worker-agent-gone warnings and left two others alone, though all six had been
  stopped by the supervisor the same way: a control exit, a last status line that is a declared `paused` line with prose, no busy state. The four had their panes left open; the two did not. The cause was the
  rule below, which read a `paused` line as a wait only when it stated a time or a length, so a pause written in prose was not-a-wait: wrong, because in the supervisor's vocabulary a status line that
  starts with `paused` is by definition a declared wait. Now the lane's newest status line being a `paused` declaration (or `captain-held`, which the supervisor reads as the same kind of wait),
  written after the lane's newest agent start and not past the time or length it states, is a declared wait at ANY pane state, open or gone: an `info` row naming the wait text (its first 80 characters)
  and no incident. It stays an incident when the newest line is `blocked` or `needs-decision`, when the declaration is older than the agent start, or when the wait is overdue by the existing wait-limit rules; a
  declaration whose time cannot be placed against the agent start (no `[at=]` stamp, or a lane record with no session start) is an unknown row with the reason, never a guess, unless the lane's pull-request records show it waiting.
- **Read the way the supervisor reads it.** The reference adapter (the doctor core does not depend on the supervisor) asks the home's own classifier (its declared-wait, paused and captain-held checks) in ONE bounded (`adapter_config.classifier_s`, 3 s), read-only, non-interactive `bash` call per run, which only defines functions and reads the status logs; it
  is never sourced into the core. With no classifier in the home, or a call that fails, an in-adapter mirror of the same rule reads, and the check's detail says which one read. A status log neither can read is an unknown row with the reason.
- **Corrected: the earlier narrower reading.** The "waiting, agent gone, with the wait recorded" entry below, its fixture expectations, the catalogue (item 29), `docs/incidents.md` and the adapter's README
  said a plain `paused` line, or a pause that names nothing, stays an incident; they now say it is a declared wait. A `paused` line with no stated time or length used to be carded as "records no time, length or
  pull request"; it is an info row now.
- **Proof** (`scripts/test`; no other new tests): six lanes shaped like the real ones (neutral names, a control exit, a prose `paused` line, no busy state, four panes open and two gone) read as six info rows and no
  incident, through the adapter's mirror and through a classifier stand-in; the look-alikes that stay incidents (the supervisor owed an answer, a declaration from before the agent start, an overdue wait, a
  working or `blocked` line after it, an unstamped declaration and a lane record with no session start as unknown rows); and a drift scenario that judges the classifier's documented cases one lane each (a
  `resolved` for another key, for its own key and keyless, continuation prose, an unknown prefix, a whole-word and case-sensitive verb, a correlation token, a legacy free-text event, `captain-held`) by the mirror
  and, when `FLEETDOC_FM_CLASSIFIER_HOME` names a home that has the library, by the real classifier, so a change on either side fails a scenario. The fixture expectations that encoded the old reading are
  corrected. Mutations of the mirror (the key rule, the unknown-prefix event) and of the agent-start rule were each caught.

Install findings: running the incident rules on a Mac, as a scheduled advice-only agent every two minutes, showed three defects on the install side.
Advice-only still.

- **A PATH in the plist** ([docs/advice.md](docs/advice.md#scheduled-runs-on-a-mac)): launchd gives an agent only `/usr/bin:/bin`, so the
  pane-inventory tool and `sysctl` (`/usr/sbin`) were not found. The LaunchAgent now carries `EnvironmentVariables` `PATH` =
  `/opt/homebrew/bin:/usr/local/bin:<the installing user's home>/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin` (the home expanded at install time).
  A reinstall replaces an older agent.
- **An unreadable boot time is unknown, never a wrong count.** With no `sysctl`, the boot time could not be read, no status line or busy hook could be
  dated, and the agent-gone rule read six lanes gone where three were. Now a lane whose verdict depends on the boot time (it has a status line or a
  busy hook) is an unknown row naming the lane and the reason, in the agent-gone rule and in the parked-gate rule (item 34).
- **`ProcessType` is `Standard`, not `Background`.** `Background` throttled the scheduled run until the agent-presence rule answered `unknown: the
  tick time budget was used up before the pane list was asked`, while the same check took half a second by hand. The cause is documented. The rule also
  asks its ONE pane inventory call per backend first, under its own allowance (`adapter_config.inventory_s`, 3 s), so the other checks of the tick
  cannot starve it; the tick budget itself stays as it was and an overrun still shows in `cost.over_budget`. No call is added; the cost block in
  [docs/incidents.md](docs/incidents.md#latency-and-cost) says so.
- **Waiting, agent gone, with the wait recorded: info, never an incident** ([docs/incidents.md](docs/incidents.md)). A lane whose agent is gone and that has
  itself recorded a wait a relaunch would not end is shown as an `info` line naming the wait and since when (under the check, in `advice.txt` under the incidents, and in
  `doctor.json` `checks[].info`, schema 1.6), no card, no ledger row. Only structured records count, never prose: a declared pause that states its time (`until`) or its length and is
  not past it; the lane record's `pr=` with no merge recorded for the task, while the lane's latest counted declaration is a plain pause or none (one rule with the status line: a later
  `working` or `resumed` ends it); a status line newer than the lane's newest session that carries a pull request's address, with no `working` or `resumed` after it. A declared pause past its stated
  time is an incident whatever else the lane recorded (`pr=` or a status line). A lane back at work, a task the fleet ledger dates a ready pull request for (judged by the done-and-waiting rule below), and a merged pull request with no declared wait stay incidents. (A pause that names nothing is a declared wait too: corrected above.)
  One rule for the lanes a supervisor stopped on purpose with a declared wait, which the rule flagged inconsistently on a second machine. From the repo and its fixtures alone the difference
  cannot be proved: the earlier exemption read only the fleet ledger's `task.pr_ready` and a `done` or PR-ready latest state, so a stopped lane with a recorded `pr=` or a declared pause but no
  ledger record was flagged and one with the ledger record was not. A standalone `fm-control exit` leaves no durable record of its own (it only retires the busy sidecar, an absence), so
  "a recorded control exit" is not read; the declared wait the lane itself recorded is.
- **A lane waiting for the supervisor says so.** The card of an agent-gone lane with an open `needs-decision` or `blocked` now reads `waiting for the supervisor to answer ..., agent gone, relaunch needed`,
  from the declaration's kind. A lane waiting on the supervisor should record
  `needs-decision` or `blocked`, so the rule can name who it waits for: a `paused` line is a declared wait, whatever its prose, and stays `info` until it passes its own stated time or length.
- **The work-at-risk answer has its own allowance** (`adapter_config.git_probe_s`, 2 s per git read verb, like `inventory_s` for the pane list). A tick the other checks had used up answered
  `unknown: the tick time budget was used up before git was asked`. Measured on a synthetic three-lane home with a tick budget of 1 ms: before, `unknown` for all three lanes, `cost.took_ms` 10 to 11;
  after, read for all three, 17 (about 2 ms a lane for its two git verbs); with the default budget 17 to 18 before and after. The tick budget is unchanged and an overrun still shows in `cost.over_budget`.
- **The knowledge-mirror rule finds the clone without a config key.** With no `adapter_config.knowledge_mirror` it reads the user's crontab (one read-only `crontab -l`) for a line that runs the sync
  (only a line that invokes the knowledge-sync script by name, a file name containing `knowledge-sync`, with its clone as `--repo`; `--ns` is the namespace, the log is the line's own redirect
  else `<clone>-sync.log`; another line naming knowledge, or a redirect to a log alone, never counts) and the default places directly under the home (two fixed names, in the adapter's README),
  and checks every clone it finds; the declared key still wins. With none, the skip says where it looked and which key to set (`advice.txt` now carries that reason instead of "nothing applies here").
- **Proof** (`scripts/test`; no other new tests): scenarios for each recorded wait and its look-alikes (a plain pause, an overdue one, a pause owed to the supervisor, a pull request then back at work
  (by status line, by `pr=`, and by a `resolved` closing an open decision), a `pr=` with an overdue pause, a merged one, a status line older than the session), a used-up tick budget with the work-at-risk answer still read, and a clone found by default place, by its knowledge-sync crontab line (a log-only line and an unrelated `--repo` line ignored), none found
  with the key hint, and a declared key winning. Each fix was also broken on purpose and the matching scenario failed.
- **Done and waiting on an upstream pull request is not an incident.** A lane whose latest recorded state is `done` or PR-ready, with the PR ready for
  review and not merged (the status log and the fleet ledger's `task.status`, `task.pr_ready`, `task.merged`: no new source), expects no agent. A
  `working` line after it, a merge, or a relaunch since ends the exemption, and the existing rules judge the lane; a lane the records cannot place
  after its agent session is unknown with the reason. Open, not-done lanes still fire. A PR closed without a merge leaves no record, so such a lane reads as waiting until it
  is cleaned up (documented).
- **Proof** (`scripts/test`; no other new tests): a readable boot time reads three open lanes gone and three waiting lanes quiet; the same home with
  no boot time reads the three open lanes unknown with the reason and the waiting ones still quiet; done-and-waiting (quiet), PR-ready by the
  ledger alone (quiet), an open lane (fires), back at work after PR-ready (fires), merged with the lane still shown (fires), relaunched after PR-ready
  (fires), cleaned up (not live), done with no PR (finished), an unplaceable lane (unknown); a tick budget of 1 ms still reads all three lanes from the one
  inventory call; a parked gate with no boot time (unknown naming the boot); the installed plist's PATH, Standard process type and no literal `~`.
  Twelve one-line mutations of the product, each caught (one needed a further scenario first). `scripts/test` accepts a `*` in a `file_has`/`file_lacks` path that matches one file.

Judged alarms: for every stall alarm the Doctor explains, one append-only verdict row, and one outcome row once the lane's window has passed:
what the lane did next, and whether the verdict was wrong, so that the wrong-benign count is a number we read.
Advice-only: nothing acts on a row.

- **The ledger** ([docs/judged.md](docs/judged.md)): `<state>/judged.jsonl`, in the value ledger's family (`v`, `id`, `tool`, `host`, `read_at`,
  `reader`, `supersedes`; an unknown written as unknown with its reason). `fleet-doctor advise` appends a **verdict** row for each alarm it explains
  (the verdict, the sources it cites, and how old each of the lane's state sources was at the alarm's moment, `null` with its reason when a source
  gave no time) and, once 30 minutes have passed, a **new outcome** row that names it. A verdict's id is the alarm's time and a hash of its text
  (`verdict:<at>#<sha1[:8]>`), never its number in the watcher's log nor the lane it resolves to today, so a log rotated from the head or a lane
  cleaned up since records no alarm twice; identical repeat deliveries of one alarm are one row, so the judged count can be below `advice.json`'s. A row is never edited; at 16 MiB the ledger refuses new rows
  and says so; `uninstall` leaves it in place and names it.
- **The follow-up** is deterministic and runs in the same advise run, on records it already read (the reference adapter's new `Source.outcome`:
  the lane's status log, terminal outcomes, lane map, the fleet ledger and the slot log, plus the names and times of the files in the lane's own
  inbox): the lane was `relaunched`, `gone`, `finished`, `answered`, `moved`, `stuck` (quiet, and a later real alarm of its own) or `quiet`. Silence
  alone is never a stall: a quiet lane waits up to 6 hours for a later alarm, then is undecided.
- **The wrong rule**, a fixed table in the docs: a benign verdict followed by a stall that persisted past the window or by a relaunch is
  **wrong-benign**; a real verdict (`wedged`, `dead`) where the lane moved on inside the window with no relaunch and no instruction recorded is
  **wrong-real**; anything not yet decidable or lacking evidence is **undecided** with its reason, and is never counted as agreed. A person acting
  at the pane leaves no record, so wrong-real is an upper bound; `waiting-on-human` is never wrong-real.
- **The number a person reads**: `fleet-doctor judged [--days N] [--json]` prints alarms recorded, explained, real, unknown verdicts, agreed,
  wrong-benign, wrong-real and undecided (each undecided reason counted) straight from the rows, and says `unknown` with the reason when there is no
  ledger, or marks the counts as a lower bound when the ledger starts after the window does. The 14-day line is also in `advice.txt` and
  `advice.json` (`alarms.judged`). `stall-explain --replay` judges against a replay's hand-labelled outcomes the same way, offline.
- **Cost.** No process is added. On the fixtures' synthetic home (21 lanes, 105 alarms) the first run appends 105 verdict and 105 outcome rows
  in 2 ms and later runs take 1 ms, in an advise run of about 20 ms; a verdict row is about 0.7 KB, an outcome row about 0.4 KB; the ledger is read
  once per run, about 11 ms for each MiB. The one new read is the names and times of the files in a lane's own inbox.
- **Proof** (`scripts/test`; no other new tests): two advise runs with the watcher's delivery log trimmed from the head between them still hold
  one verdict row per alarm; the reference adapter's restart burst, replayed as it stands (no wrong verdict, the three healthy
  look-alikes agreed) and replayed with the evidence of the rules before that burst (nine stalled lanes called a declared wait, then relaunched:
  nine wrong-benign, the look-alikes still agreed). Three one-line changes to the table each fail the suite. The `uninstall` scenarios now expect the
  ledger left in place, as for `incidents.jsonl`.
- **Honesty.** The words are the records' own (explained, relaunched, finished, moved); the docs say what the records cannot know: a person acting
  at the pane, a quiet lane that is only quiet, and a verdict judged as it was given, not as today's rules would give it.

Incident cards: one append-only row for each failure that has actually happened. Advice-only: nothing acts on a
row, and a row's next step is text. The finding schema is 1.2 and `doctor.json` 1.5 (additive).

- **The rule family** ([docs/incidents.md](docs/incidents.md), catalogue 29 to 34, in the reference adapter): a worker's agent gone while its
  lane record, worktree and window are alive (`worker-agent-gone`; the relaunch command that keeps the worktree is its next step); the
  supervision watcher down or its beat stale (`watcher-down`, and the existing `watcher-stalled` now carries a card, so one failure is one row);
  the knowledge mirror's push failing, its sync stopped, or its clone stuck mid-rebase or detached (`mirror-push-failing`, `mirror-stale`,
  `mirror-repo-stuck`); a machine the dashboard's own feed calls NOT ANSWERING (`machine-not-answering`); a project clone of a pipeline home
  that resolves no git identity in any scope (`project-identity-missing`); a pipeline run parked at a gate nobody drives (`gate-undriven`). Each reads only
  what supervision already computes (lane records and hooks, status folds, the process and pane lists, the run ledger, logs and feed files): no
  model call, and no new tool.
- **The row** (`fleetdoc.incident 1.0`): what happened, since when (and from which record), what it blocks, whether work is at risk (`yes`, `no`
  or `unknown`, and why), the records it rests on with their ages ("age unknown" when a record has no time), and the one safe next step as text.
  It is appended once, when the incident is first confirmed, to `<state>/incidents.jsonl` (append-only, one line per record, never rotated,
  trimmed or edited; at its size limit it refuses new rows and says so); `<state>/incidents.json` is what is open now. `uninstall` leaves the
  ledger in place and names it.
- **Latency.** A worker's agent is carded after 3 minutes without a sign of life (`adapter_config.agent_gone_after_s`, 180 s). The worst-case
  wait is that threshold plus the run cadence; `fleet-doctor install --home HOME --adapter NAME --advice-only --every 2` makes it 3 to 5
  minutes (a run costs 50 to 90 ms). The default cadence stays 10 minutes.
- **Unknown is a row, never silence.** A rule that cannot read its input (no process table, a backend pane list that cannot be read, a run ledger
  of another shape, a feed that is not JSON, a clone that is not a repository) or that breaks writes an unknown row naming what it could not read;
  a stale feed makes NOT ANSWERING last-known; a rule with nothing declared is `skipped` and listed. A declaration older than the last boot or the
  lane's session counts for nothing (the explain engine's floor, reused as `engine.stale_declaration`).
- **Where it shows.** Findings with a card print `incident:` and `next:` under `check`; `doctor.json` has an `incidents` block and
  `cost.incidents_ms`; `advice.txt` lists the incidents before the alarms, `advice.json` holds them, and the card says `N incidents open`
  (`warn`, `bad` while a critical one is open); a Lookout feed sends each as an attention card with the plain words and the next step.
- **Cost.** Inside the existing 2 s local budget: 50 to 90 ms for the six rules on the fixtures' synthetic homes (ten lanes, nine rows), the fold
  and append under 1 ms; one read-only pane query per silent unfinished lane (at most 12 a tick, the rest reported unread) and two git read verbs
  per lane with a card are the only extra work.
- **Proof** (the reference adapter's incident fixtures, replayed by `scripts/test`; no other new tests): every rule has its failure and a healthy
  look-alike that differs by one fact; a Mac whose panes answer and one whose pane is mute; every kind of unreadable input; the ledger kept
  append-only; `--no-write` writing nothing; live repair on changing nothing; a mutation run of one-line changes to the product, each caught
  (the reference adapter's results notes).
- **Honesty.** README and docs say what the rules cannot know: an agent stopped on purpose looks the same until the lane declares `done`; "the
  window is open" is shown only when the pane answered; a machine whose source went quiet is the dashboard's own derived word, not in a feed file.

Incident rules, first live run: two defects fixed (catalogue 29 and 33; advice-only).

- **`project-identity` judges the identity the pipeline would commit with.** It fired for project clones although the account has a global git identity, so the review pipeline's fix commits worked: a false alarm. It now asks git itself, in each clone, as the user that runs the Doctor (which must be the user that runs the pipeline daemon), with every scope resolved (local, global, system; the last entry wins) and fires only when `user.name` or `user.email` resolves empty (a key set to nothing is empty). When it is fine the check's detail says which scope supplied each part (`name from global, email from local`). Docs say whose identity is judged.
- **`worker-agent` reads one backend inventory per run, not one probe per lane.** The old per-lane `pane process-info` question, capped at `agent_probe_max` and bounded by the tick budget, left every quiet lane `UNKNOWN` ("not asked: the probe cap or the tick budget") at a dozen lanes. It now makes one `pane list` call for lanes whose backend lists panes with their registered agents and statuses, and one `list-panes -a` for tmux lanes (records with no `backend=`), once per backend and session however many lanes ask, counted once against the tick budget. A lane is `unknown` only when its backend's inventory could not be read or does not list its pane, with the exact reason. A pane the inventory lists with no agent registered is confirmed on Linux and WSL by the process working in the worktree (an agent there is alive, and the check's detail says the backend had not registered it; an unreadable table is unknown); on a Mac the inventory is trusted. A registration that outlives its agent stays quiet: a missed alarm, not a false one. A tmux pane's own process counts as its agent. `adapter_config.agent_probe_max` is removed (it capped nothing any more); `adapter_config.tmux_bin` is new. Measured on a synthetic twelve-lane home: 8 backend calls to 1, `cost.took_ms` 82 to 95 before, 12 to 13 after (a fixture shim, not a live backend).
- **Proof**: fixtures for a global identity with no local one (quiet, names the scope), an identity in no scope (fires), a name without an email and an emptied local email (fire); twelve lanes with all agents present (all fine), the same with one gone (exactly that lane), two backends (one call each) and an unreadable backend (unknown with its reason); a pane with no agent registered and an agent in the worktree (quiet), none in either (fires), an unreadable table (unknown with its reason); a tmux pane whose own process is the agent (quiet); 15 one-line mutations of the product, all caught.

The demo fleet no longer leaves its children running.

- `examples/demo-fleet/make_demo.py`: the busy and idle children stop by themselves after 60 seconds (was 10 minutes) and the busy
  one runs at nice 19. Their pids go to `DIR/pids.json`; `make_demo.py --stop DIR` ends exactly those pids, each only while it is
  still the recorded process, and removes the file. `scripts/test` calls it when the run ends, also on failure.

Scrub: the shipped examples name no real host, machine, project or mate.

- The reference adapter's `farm_markers` and `farm_backing` defaults and the Windows `leaks.servers` default are now neutral examples
  (`tests/project-a-farm`, `game-server-standalone`). A home that relied on the old defaults must now set `farm_markers` and
  `farm_backing` in its adapter config: a process whose command line contains a marker is treated as farm work, and a server under
  it or its parent as its backing (and, on Windows, `doctor.windows.leaks.servers` for its server's executable name).
- The reference adapter's default scratch groups are neutral examples too: the headless Chrome profile group
  (`project-a-chrome-profile-*`, beside `puppeteer_dev_chrome_profile-*`) and the test-floor data directory group
  (`project-a-test-floor-<ms>-<pid>-<n>`). A home whose scratch directories use other prefixes must now configure those two groups
  in its adapter config's `scratch_groups`.

Stall-explain no longer calls a stuck lane benign after a restart. Advice-only: nothing is repaired.

- A declaration of any kind (a declared wait, and `done`, `failed`, `blocked`, `needs-decision`) counts only when it is newer than
  the machine's last boot and than the lane's newest agent session (`host.boot_at`, `lane.session_at` in the evidence); otherwise
  `[stale-declaration]` and the later rules decide, so it is never `declared-wait` or `waiting-on-supervisor`. Evidence recorded
  before these keys is judged as before.
- A past alarm's lane map is read as of the alarm: the fleet ledger's cleanup and dispatch events and the entry kept from before a
  relaunch. A lane that cannot be reconstructed is `unknown` with the reason (`lane.known: false`), never `torn-down`.
- An unread instruction the watcher could not deliver because the agent exited is `wedged` (`doorbell-undelivered`), not a busy deferral.
- A busy hook line from before the last boot or the lane's newest agent session is not read. The fleet ledger is read only for
  rows whose `event` is exactly `task.dispatched` or `task.cleaned_up`. `core/proc.py`: `boot_history`, `last_boot`; `boot_epoch` also reads
  `sysctl kern.boottime`.
- Reference adapter: `adapter_config.boots`, `only` and `title`; the restart-burst replay file (13 real stalls
  and 3 look-alikes) and its labels. `scripts/test` takes every replay fixture's own counts.


Advice on a schedule, per home. Advice-only: nothing is repaired.

- `fleet-doctor advise`: the catalogue check, always in advise mode (no repair, no plan, whatever the live-repair toggle says), plus
  `stall-explain` over every alarm the source recorded in the window (`advice.window_s`, 24 h), each as of its own moment. A lane
  whose newest alarm was real or unknown is asked about once more, as it is now. The run writes `advice.json` (`fleetdoc.advice`
  1.0), `advice.txt` (plain lines for a supervisor at wake time) and `card.json`, a `lookout.card` 1.0 card with alarms explained,
  real and unknown, the top explanations and the lanes still open (`tool` `stall-explain[-<label>]`; also written to `advice.card`).
  `--no-write` writes nothing. Code: `src/fleetdoc/doctor/advice.py`.
- `fleet-doctor install --home HOME --adapter NAME --advice-only`, `installed`, `uninstall`: one entry in the user's own scheduler (a
  marked crontab line on Linux and WSL, a LaunchAgent on macOS). Nothing system-wide, nothing restarted, no shared configuration
  edited. Every other crontab line is kept byte for byte, and a copy is kept. Idempotent; `--dry-run`. The install has no repair
  option and writes no live-repair record. Its files live in the home (the adapter's `ADVICE_DIR`). Code: `src/fleetdoc/doctor/install.py`.
- Sources may offer `recorded_alarms(since, until)` and `ADVICE_DIR`; the reference adapter does (`export_replay` now uses the same
  method).
- `scripts/test` also removes its temporary directory when the suite is stopped with SIGTERM or SIGHUP (a pipeline's timeout). New
  file expectations (`file_has`, `file_lacks`, `file_equals`, `file_count`, `json_file`, `card_valid` against the board's card rules,
  `glob_count`, `unchanged_but`), and the reference adapter's `fixtures/advice_fixture.py`: the window's counts and kinds, the card, the
  kill switch, `--no-write`, install through `crontab` and `launchctl` shims (dry run, refusals, a second install, uninstall back
  to the exact crontab), and an advice run with live repair on that repairs nothing.

Doctor: ssh additional-check prompts (item 28). Advice-only: no repair is added.

- Core: `ssh-check` (28, `ssh-check-required`): one non-interactive, bounded attempt per ssh alias (`BatchMode`, connect timeout, `ControlPath=none`, hard
  timeout, the no-op `echo`), sorted into ok, additional check, refused login and no answer. A server that prints a Tailscale SSH "additional check" prompt
  and a login URL is reported as the operator's to clear, with the URL and what waits behind it (steers, pushes, job channels). The URL is never opened and
  the check never completed; ssh config, keys and policy are never touched. Aliases: `doctor.hosts` and their guests (through the probe `hung-host`
  already makes, so no second connection), `doctor.ssh_check.aliases`, and with `from_ssh_config` the plain `Host` names of the operator's ssh config
  (`Include` followed, wildcards and `HostName` lines skipped, `exclude`, `max_aliases` 24). Unknown when no alias answered. `docs/ssh-check.md` documents the check period
  (`checkPeriod` of the `check` action in the policy file) as operator guidance.
- `hung-host` and `wsl-vm` no longer call a host or guest hung because its login waits for an additional check; the ssh probes use `LogLevel=INFO` so a quiet
  level in the operator's config cannot hide the server's banner.
- `doctor.json`: `local.ssh-check` (per alias `state` and `url_seen`; `not_tried`); same schema version.
- `scripts/self-audit`: the product name "Tailscale" is no longer on the vendor list (the probe recognises that product's prompt); the names that can carry someone's private network stay on it.

Doctor: the Windows platform under the lanes (catalogue 23 to 27), from the 2026-10-10 incident in which a Windows Update driver restart
left virtualization off in firmware and every WSL lane on the machine down for about four hours. Advice-only:
no repair is added, and nothing changes firmware, boot, policy, service or scheduled-task settings or installs anything.

- Five core checks read one new read-only PowerShell script (`windows/platform.ps1`): `firmware-virtualization` (23: no hypervisor and
  virtualization off in firmware while a WSL 2 guest is expected, critical with `drain`; firmware on but no hypervisor), `hypervisor-launch`
  (24: Hyper-V-Hypervisor event 41 at this boot, critical with `drain`, or at a recent boot, with the restart that led to it and the driver or
  firmware updates around that restart), `pending-reboot` (25: the Windows Update or servicing restart key, the active hours, the driver updates
  staged for the restart), `update-restarts` (26: Windows Update restarts in the last 24 h with their driver updates and whether the next boot lost
  the hypervisor; unexpected restarts) and `update-policy` (27: active hours, drivers through Windows Update, deferral, pause; information only).
- They read this machine's own Windows side (from a WSL guest through `powershell.exe`, or on Windows) and every Windows host in `doctor.hosts`,
  where the script rides on the existing ssh probe (one call per host and tick). Settings under `doctor.windows.platform`; `expect.wsl2` on a host.
- `doctor.json` 1.4: per Windows side `platform`, `virtualization`, `hypervisor`, `restarts`, `update_policy`, and `reboot` as
  `{pending, due_epoch, source}`, the shape a Harbor `harbor.beat/1` record carries; the hand-off is documented, Harbor is not changed.
- `wsl-vm`: a silent guest whose error says `HCS_E_HYPERV_NOT_INSTALLED` is reported as unable to start for want of a hypervisor, not as stopped.
- New `docs/windows-platform.md`: what is read, the look-alikes that are not findings, the diagnosis recipe from the incident, the policy
  advice, the Harbor hand-off, the settings.
- Fixtures: 19 synthetic scenarios (the outage over ssh and natively, a recovered failure that ages out, the firmware flag under a running
  hypervisor, no WSL 2 expected, firmware on without a hypervisor, a pending restart and its softer-sign twin, unreadable keys, a person's and a
  script's restart, an unexpected restart, the policy, no interop, an older probe, cards, live repair on changing nothing, `--no-write`). A
  fake `powershell` and the fake `ssh` share one read-only guard (`psguard.py`, now also refusing `Install-`, `Invoke-`, `Update-`, `Write-`,
  `bcdedit`, `reg add`, `schtasks`, `dism` and a placeholder left in a script). `scripts/test` removes its temporary directory (`--keep` leaves it).

Doctor D4: the further catalogue (items 11 to 13, 15, 17 to 19, and three new ones from 2026-10-09). Advice-only: no repair is added.

- Core: `wsl-interop` (19: no `WSLInterop` entry in the guest's binfmt_misc table, so Windows programs cannot start) and `scratch-leaks` (20: entries of
  configured scratch groups that no live process uses, with sizes, oldest age and what was checked; a RAM-backed volume at 90% or more, critical at 97%,
  which flags the host to drain). Reference adapter: `git-identity` (11), `tool-version-skew` (12: a declared minimum version, and a pipeline
  worktree in use with a `prepare` step in its config and no `node_modules`), `slot-config` (13: the slot manager's caps file and exported caps it rejects),
  `done-claims` (15), `env-leak` (17), `meta-order` (18: the merge poll's `pr=` block rule) and, from the 2026-10-09 snags, `slot-holders` (21: a slot held by a
  process other than the one its record names) and `idle-farm-process` (22). Each is optional (`doctor.disabled`) and says `skip` until it has something to judge.
- `doctor.json` 1.3: a `local{check}` block (RAM-backed volumes and scratch groups, slot records per class, the interop entry). A critical full tmpfs sets `drain`.
- The Lookout feed takes at most 20 collectors: with more than 20 checks the last ones share one `more-checks` collector instead of being cut off silently.
- `execute.run` takes `None` in `env` to remove a variable from the child (the identity check must not inherit the Doctor's own author variables).
- Fixtures: every rule has a healthy look-alike beside it (a browser profile named by a live command line, a working directory or an open file; a younger entry; an
  entry whose owner pid is alive; a longer path that only shares a prefix; a slot held by its own job, a stale record, a job taking the slot now; a farm process with a
  server under it, under its parent, or too young; a worktree that holds its dependencies; a landed lane's deleted branch; a just-started test that sets an override;
  a gate that has not run for a month); a Mac scenario; live repair on changing nothing; read-only proofs by digest. A mutation run of 16 one-line changes to the product
  was caught by `scripts/test` every time (the reference adapter's results notes).

Doctor D3: the Windows side. Advice-only: no repair is added.

- Three checks read one shared, read-only PowerShell probe per Windows host (sent as one base64 line on standard input; run in memory;
  `wsl.exe` is never called): `wsl-vm` (catalogue 2: a guest silent while its VM runs is `host-hung`, while the VM is down
  `wsl-not-running`; the VM near its `.wslconfig` cap; the VM spinning on a host short of memory), `windows-leaks` (16: parentless
  `game-server-standalone`, and node, chrome and headless browsers only when marked as farm work, with child trees and memory; orphaned
  session counts) and `windows-capabilities` (14: login shell and profile, graphics adapters and per-app GPU preference, tool versions,
  desktop session, against a host's optional `expect` block).
- `doctor.json` 1.2: a `windows{host}` block with what each check saw; `flags.capability_drift` is now set (`false` when a declared host
  matched, `true` on drift, `null` when nothing is declared). Findings are Lookout cards as before; a critical near-cap or spinning VM sets `drain`.
- A Windows host's guest check moved from `hung-host` to `wsl-vm` (same finding id, with what the Windows side sees added). The host
  probe no longer travels as an encoded command (too long for cmd.exe).
- Fixtures: a recorded real version-2 probe answer and answers derived from it, twins for every rule (the unmarked browser, the
  unlisted name, the few sessions, the roomy host, the answering guest, the stopped VM, the older probe), a transport twin in the ssh
  shim that refuses a probe script with a mutating verb, live repair on changing nothing.

Optional status card for a dashboard.

- `fleet-doctor card [--card PATH]` prints (or writes) one `lookout.card` 1.0 JSON card from the last check, and `check --card PATH`
  writes it after a check. Off unless asked; no dashboard import; the card carries `repair_mode` as `mode` (`advice-only` or
  `live-repair`). `--stale-after SECONDS` sets how long it may be called current. Code: `src/fleetdoc/doctor/card.py`.

Live repair as the operator's per-home toggle.

- `fleet-doctor live [status]`, `live on`, `live off [--by NAME]`: one explicit switch per home, recorded with who and when in
  `<state dir>/live-repair.json` (`fleetdoc.live-repair 1.0`, scoped to host, adapter and home) and in `actions.jsonl`. **Off by
  default** (advice-only). A missing, unreadable, off or other-scope record is off. Nothing but this command turns it on: not
  configuration, not an environment variable, not a default, not the installer, not an adapter.
- `mode: repair`, `--set mode=repair` and `FLEETDOC_MODE=repair` are now usage errors (live repair is not a setting); `mode`
  takes `off` or `advise`. Fixtures that repaired now start with the operator's `live on` through the public command.
- With it on every gate is unchanged and the kill switch wins. The toggle is read again before reading 2 and before the act, so
  `live off` stops a repair in flight. `check --no-write` still acts on nothing. `approve` is a person's act and works either way.
- The mode shows as `advice-only` or `live-repair` in `status` (text and `--json`), the `check` header, `doctor.json`
  (`repair_mode`, `live_repair`, `repairs.repair_mode`) and the Lookout source label; the Doctor needs no dashboard.
- Fixtures: off by default, only the command turns it on (env, `--set`, config key, another host's record and a garbage record
  all stay advice-only), turning it off returns to advice while plans and `approve` still work, `live off` between the readings
  stops the repair, the kill switch beats the toggle, `--no-write` with it on writes nothing.

## [0.3.0] - 2026-10-09

Doctor D2: the first safe repairs, only by calling the owners' own commands. Local only, not published. Proven on
synthetic homes and shim owners; not run against a live fleet.

- Modes `off`, `advise` (default) and `repair`. In advise mode a repair is reported as what it would do and nothing runs.
- Tier-1 repairs in repair mode, each through gates (kill switch, a host circuit breaker at 4 open findings or a critical
  host, a per-action breaker after 2 failed or unverified runs, flap detection at 3 repairs in 24 h, rate limits of 3 per
  host, 2 per action and 1 per finding per hour, a Doctor-wide lock), two readings of the owner's dry run and of the finding
  (the second immediately before acting, with the same targets required), one run of the owner's command, and a
  verification that the check no longer finds the snag.
- Tier-2 plans (`fleetdoc.plan 1.0`, inert JSON files under the state directory) and `fleet-doctor approve PLAN_ID`, which
  re-verifies everything (state, expiry, the closed action set, the argv the Doctor would build now, the owner's list) and
  runs exactly that argv once; owner-run plans are handed over and nothing runs. `fleet-doctor plan`, `plans`, `reset ACTION`.
- Configuration can hold an action back (`doctor.repair.actions.<name>`: `plan` or `off`) and never widen one.
- Every repair, refusal, plan, approval and reset is a line in `actions.jsonl` with its reason and both readings; every
  run and refusal of a tick is a Lookout card, and every plan waiting for a person a `decision` card.
- Item 4: a gate ahead of its lane because the lane's own running pipeline pushed fix commits there is healthy, not a
  finding (the gate head is the head the running run holds or last pushed, read from the run ledger opened read-only). On
  the real home this was all three D1 gate findings. Fixtures: the mid-pipeline look-alike, and twins where a finished run
  or a running run holding another head still leaves the finding.
- Repair mode against a live fleet needs the operator's explicit go; proven on fixtures only.
- `fleetdoc.finding 1.1` adds `target` (what a repair would act on, as the check saw it); `fleetdoc.doctor 1.1` adds the
  `repairs` block.
- The reference adapter's five actions: reap job workers whose code root is gone (T1), reload the job worker through its
  readiness owner when every gap is the worker's (T1; a session or launch-agent gap makes it a plan), prune merged, clean,
  unused pool worktrees (T2), bring a lane level with its review gate (T2, handed to the lane's worker), re-arm a stalled
  watcher (T2, handed to the supervisor).
- Fixtures through the public command: every tier-1 action with refusing twins (a healthy worker in the owner's list, a pid
  recycled between the readings, an empty list, a session gap, another account's queue, a gap only a person can close), the
  kill switch thrown three ways, both circuit breakers, the rate limit, flapping, approve refusing a changed, edited, used or
  stale plan, and recorded real output of the pool tool's prune parsed as it prints it.

## [0.2.0] - 2026-10-09

Doctor D1: advise-only detection of catalogue items 1 to 10. Local only, not published.

- `fleet-doctor check`: twelve checks, one line each with a mark; findings with evidence, a plain explanation, the owning
  script's dry-run command as the "how" and a recommendation. `-v` for evidence, `--json`, `--only`, `--watch`, `--no-write`.
  Exit status is the worst result (0, 10, 30, 50).
- Items: remote job workers (duplicates, stale code identity, lock disagreement, abandoned roots, orphan lanes), hung or
  out-of-memory hosts over ssh (with a read-only PowerShell probe for Windows hosts), dead remote channels, stale review-gate
  refs, clobbered checkouts, parked lanes and shared worktrees, pool slot claims, duplicate watchers, disk, memory and swap,
  orphaned servers. Tool-specific checks live in the reference adapter's `doctor.py`; the core names no tool.
- `doctor.json` (`fleetdoc.doctor 1.0`) with per-host `drain` / `hung` / `disk_critical` flags (`null` means not checked) and
  the Doctor's own cost. `--push` publishes findings to Lookout as cards, one collector per check, and `draining` / `lost` health.
- For and clear durations (`doctor.for_s`, `clear_s`): a finding must be seen for its duration before it counts.
- `--dry-runs` (opt-in) runs the owners' dry-run commands and attaches their output; nothing else is ever run.
- Fixtures through the public command only: synthetic `/proc` and home trees, recorded real Windows probe output behind a
  fake `ssh`, real git repositories for the gate cases, and look-alike worlds the Doctor must leave alone (`scripts/test`).
- The process inventory, memory reader and bounded command runner moved into `core` (`sysinfo`, `execute`); `core.push`
  gained `deliver` so any tool can publish a feed as a source of its own.

## [0.1.0] - 2026-10-08

First slice: the repository skeleton (C0) and the Stall-Alarm Explainer in advise mode (E1). Local only,
not published.

### Added
- `stall-explain LANE`: a one-line verdict with the evidence behind it, usually in a few milliseconds
  (a CPU-sampled lane takes about 0.4 s), `--json`, `-v`, and an exit code per verdict.
- Verdicts: `queued`, `running-long`, `running-remote`, `thinking`, `declared-wait`,
  `waiting-on-supervisor`, `waiting-on-human`, `wedged`, `dead`, `torn-down`, `unknown`; qualifiers
  `slow-host`, `host-unverified`, `busy-attested`, `stale-record`.
- `--replay FILE` and `--demo`: run the engine over recorded alarms and score it against hand labels,
  with an accuracy gate (`--gate`).
- The generic `files` source (a hand-written `lanes.json`) and an adapter contract
  (`adapters/<name>/source.py`).
- The reference adapter (`adapters/`, for the agent supervisor this was built beside): lane map, status logs, slot jobs and queue, review-pipeline step ledger,
  terminal outcomes, the recorded stale alarms, and `--export-replay` with redaction. Includes the
  redacted fixture of 63 real alarms from 2026-10-08 with hand labels.
- Core: config with `--set`, host identity, process identity (pid plus start time plus command line),
  `lookout.beat` reader, the `fleetdoc.verdict` schema, the actions log, the kill switch, Lookout push.
- `fleet-doctor` front door: `status`, `off`, `on`, `version`, `check` (catalogue arrives in D1).
- `scripts/self-audit` (external denylist), `scripts/build-package`, `scripts/test` (the approved
  fixture and smoke set).

### Known limits
- Linux and WSL2 are the only platforms this was run on. The process-tree and CPU reads need `/proc`;
  elsewhere they report "unavailable" rather than a guess.
- The accuracy numbers come from the data the rules were developed against; see docs/accuracy.md.
