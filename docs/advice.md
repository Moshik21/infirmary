# The advice run and the per-home install

`infirmary advise` is one advice-only pass over a home: the catalogue check, and `stall-explain` over every alarm the source
recorded in the last 24 hours. It leaves three files that a supervisor reads when it wakes and a board shows as a tile.
`infirmary install` schedules it for one home, as one entry in the invoking user's own scheduler. `infirmary uninstall`
takes the entry out again.

```
infirmary install   --home HOME --adapter NAME --advice-only [--label NAME] [--every MINUTES] [--card DIR] [--dry-run]
infirmary installed --home HOME            # is the entry in place and the advice fresh? exit 0 yes, 1 no
infirmary uninstall --home HOME [--dry-run] [--keep-files]
infirmary advise [--json] [--no-write] [--card PATH]   # what the entry runs; also by hand
```

## What one advice run does

1. **The check, advice-only.** It runs the same catalogue as `check`, always in advise mode. It repairs nothing and writes no
   plan, even when an operator has turned live repair on for the home: repairs run only from `check`, under the operator's
   `live on`. It keeps `doctor.json` and the Infirmary's for/clear memory in the state directory, as `check` does.
2. **The alarms.** It reads every alarm the source recorded in `advice.window_s` (24 h) and explains each one as of the moment it
   was raised, which is how `--replay` scores the rules. Then, for each lane whose newest alarm was real or undecided, it asks
   once more about the lane as it is now.
3. **The incidents.** The check's incident rules ([incidents.md](incidents.md)) run with it: each confirmed failure that has actually
   happened is one append-only row in `incidents.jsonl`, and what is open now is in `incidents.json`. They need no alarm source.
4. **The judgement.** For each alarm it explained it appends a verdict row to `judged.jsonl`, and for each earlier verdict whose 30-minute window
   has passed, an outcome row: what the lane did next and whether the verdict was wrong ([judged.md](judged.md)). `infirmary judged` counts them.
5. **The advice.** It writes `advice.json`, `advice.txt` and `card.json` atomically into the state directory. When
   `advice.card` (`--card`) names a board's drop directory, the card is also written there.

An alarm is:

| | verdicts | meaning |
| --- | --- | --- |
| **explained** | `queued`, `running-long`, `running-remote`, `thinking`, `declared-wait`, `torn-down`, `waiting-on-supervisor` | not a stuck worker: a false alarm, or the supervisor's own move |
| **real** | `waiting-on-human`, `wedged`, `dead` | someone has to act on the lane |
| **unknown** | `unknown` | the evidence did not decide; never counted as explained |

A lane is **open** when its newest alarm in the window was real or unknown and asking again now does not explain it either. A
real alarm whose lane has since been explained, by a later alarm or by asking again, is listed but does not keep the card amber.

It needs a source that records alarms (`recorded_alarms`, [sources.md](sources.md)). With none, the advice says the alarms were
not read and the card is `unknown`. With the kill switch on, nothing is checked or explained, and the advice and the card say so.
On one real home, with about 100 alarms in a day, a run takes about 4 s, most of it the check.

## What a run costs, and when it steps aside

The tools cost the fleet as little as they can, so an advice run keeps a budget and steps aside when the machine is busy or running on
its battery. All of it is in `advice.budget` and can be set with `--set`; the numbers below are the defaults.

| Setting | Default | What it does |
| --- | ---: | --- |
| `wall_s` | 30 | One run's wall time. The check goes first (it has its own bounds: `doctor.tick_budget_ms` and `doctor.remote_budget_ms`); the explanations stop when the time is used up, and the run says how many alarms (and lanes asked again now) it left unexplained |
| `cpu_s_per_hour` | 40 | CPU-seconds, this process and the programs it waited for (`os.times()`), that all the runs of the last hour may use together. Past it the run does not start |
| `load_per_core_max` | 0.8 | The machine's 5-minute load average divided by its cores. Above it the run does not start. A 5-minute average, because the run's own previous pass is in the 1-minute one |
| `battery_min_pct` | 30 | On battery (not on mains) below this charge the run does not start |
| `battery_wall_s` | 10 | On battery at or above that charge the run goes ahead with its wall budget cut to this |
| `enabled` | true | `--set advice.budget.enabled=false` lifts every limit for one run by hand |
| `power_root` | none | Linux: where the power supplies are read (`/sys/class/power_supply`; never read for a synthetic `/proc`) |

**Stepping aside is a result, never a pass.** A run that does not start writes the advice anyway, and it says why in three places: the first
line of `advice.txt` (`skipped on purpose: load 0.90 per core (limit 0.80); nothing was checked or explained this run, and that is not a pass`),
the card (status `unknown`, headline `skipped on purpose: load 0.90 per core (limit 0.80); ...`, so the tile cannot look current and fine), and the
actions log (`budget` and `budget_reason` on the advise row). The other reasons read `on battery at 12% (floor 30%)` and
`cpu budget used: 41.3 of 40 cpu-seconds in the last hour`. A run that is shortened says `shortened on purpose: on battery at 80%: wall budget cut
to 10 s`, and one that was cut by its wall budget says `cut short on purpose: ... N alarm(s) and M lane(s) ... were not explained` (the card counts both) and turns an otherwise
`ok` card to `unknown`. Under `--quiet` the scheduled entry prints nothing, as before; the files and the log carry the words.

**The cadence comes back by itself.** A skipped run checks nothing, so it costs a few tens of milliseconds, and the scheduler's next entry measures
again. The first run after a back-off goes ahead in full and says `back after N skipped run(s) since <time>: the machine is within its limits
again`. The count comes from `advice-cost.json` in the state directory (the last 24 hours of runs: when, which decision, CPU-seconds, wall seconds;
the CPU budget reads it too). It is the install's own file and an uninstall removes it.

**A reading that cannot be taken is not zero.** The load comes from `/proc/loadavg` and `/proc/cpuinfo` (Linux, WSL) or `sysctl vm.loadavg` and
`hw.ncpu` (a Mac); the power state from `/sys/class/power_supply` or `pmset -g batt`; Windows has neither. When one cannot be read the run goes
ahead (the limit it would have applied is unknown, not exceeded) and `advice.txt` says `budget: load not read: <reason>` or `budget: power not
read: <reason>`; a machine with no battery is on mains, which is a fact it can read. `advice.json` carries the whole `budget` block: the limits,
the readings, the decision and its reason, the CPU-seconds and wall seconds the run measured, and how many alarms it left.

### What a run costs, measured

Method: the one the README's cost section uses, `infirmary advise` run by hand and its own cost block read: `budget.took_s` (wall) and
`budget.cpu_s` (CPU-seconds this process and its children used) from `advice.json`, and the peak resident size of the whole run from
`/usr/bin/time -f %M`. Three runs against a real home with `--no-write`, then seven persisted runs on the fixtures' synthetic home (six lanes, six alarms).

| Home | Wall | CPU | Peak RSS |
| --- | ---: | ---: | ---: |
| a real home, 13 alarms in the window, 17 checks | 2.2 to 2.3 s (the check is about 2.1 s of it; the alarms are the rest) | 0.28 CPU-s | 34.5 to 34.9 MB |
| the fixtures' synthetic home, 6 alarms | 0.02 s | 0.01 to 0.02 CPU-s | 28.7 MB |

At the default ten-minute cadence that is six runs an hour: about 1.7 CPU-seconds an hour on the real home, 4% of the default CPU budget of 40; the
wall budget of 30 s is about thirteen times the run. **Machine**: Linux 6.18 under WSL2, 24 logical cores (a laptop-class Intel CPU), 24 GB, Python 3.14,
on mains power, 5-minute load per core 0.07 while measuring. **Not measured by that method on a Mac or Windows.** The Windows readings do not exist; the budget there is the wall and CPU limits alone.

**On a Mac, from a real install's own log** (the actions log's `cpu_s` on each advise row; 8 cores, the author's supervisor home, a run every 120 s, 651 runs over
22 hours, the machine busy at a 5-minute load of 7 to 8 for much of it): a full run used **1.6 CPU-seconds on average** (median 1.5, 95th percentile 2.5, largest 3.6)
in 2.3 s of wall time, about six times the 0.28 CPU-s measured on Linux; the cause was not isolated. That install ran 0.3.0, before 0.4.1 made a Mac read its process
table with `ps` ([verdicts.md](verdicts.md#process-evidence-by-platform)), so a run on 0.4.1 may cost more and is not measured here. A run that stepped aside used 0.01 CPU-s. Of the 651 runs, 370 ran in full and 281 stepped aside: 179 because
the CPU budget (then 30) was used up, 102 because the load was over the limit.

**Why the CPU budget is 40.** A run every 120 s is 30 chances an hour, and "at least every other one" is 15 full runs an hour. At 1.6 CPU-s a run that is 24 CPU-s;
at the 95th-percentile cost of 2.5 it is 37; the budget is 40 for that, the skipped runs' own cost (15 x 0.01) and some margin. The old 30 was just enough for the
average run and not for a bad hour (resampling the 370 measured costs over thousands of hours: 19 full runs an hour on average, as few as 14); 40 gives 25 on average
and, in that resampling, never fewer than 21. The accounting itself was checked and is not the cause: a run is counted once (`os.times()` of the process plus the children it waited for, so
each `ps`, `git` or `ssh` once), and a skipped run counts its 0.01 to 0.03 CPU-s. It under-counts slightly, by the interpreter's start-up and imports before the
run's clock begins (about 0.07 CPU-s measured).

**The cost, stated.** At 40 CPU-s an hour the advise job uses at most 1.1% of one core on average over an hour (0.14% of an 8-core Mac); one run may start just under
the limit, so the worst hour in the resampling was 43.5 CPU-s, 1.2% of a core. Running every cycle in full would cost about 48 CPU-s an hour on this Mac (1.3%): the
budget still stops a runaway at about four fifths of that, and a run that gets slower (more alarms, a slow `ps`, an `ssh` that hangs) is held to the same 40 CPU-s
an hour, so the cadence thins instead of the cost growing. The load limit is a separate safeguard and is unchanged: on a busy machine (a 5-minute load per core
above 0.8, which is the case on that Mac for hours at a time) the runs step aside whatever the CPU budget is, and a larger budget does not bring them back.

## The files

All three are in the state directory, beside `judged.jsonl` (the judged alarms, [judged.md](judged.md)), `incidents.jsonl` (the ledger) and `incidents.json` (what is open now) of [incidents.md](incidents.md). An install puts that directory inside the home: the adapter names it, and `<home>/infirmary` is the fallback.

**`advice.txt`**: plain lines for a person or a supervisor, most urgent first.

```
infirmary advice · 2026-10-10T17:36:17Z · box · advice-only: nothing is repaired
home: /path/to/home
incidents: 1 open · 0 unknown · 6 of 6 rule(s) read
  [worker-agent-gone] lane-7 · warning · since 2026-10-10 17:21Z (15m ago) · no sign of life since: the lane's last busy-hook line (idle)
      what: lane-7: the agent is gone while the lane's record and worktree are still live, and its pane is still open (...)
      blocking: the lane's task lane-7 (branch fm/lane-7) has no worker: nothing will move it
      work at risk: yes: 2 uncommitted file(s) exist only in this worktree; a relaunch keeps them ...
      evidence: lane record (2s old): ...; pane / process list (0s old): pane w1:p2 answered: its shell has no agent process under it
      next: relaunch the agent in the same worktree: `<the owner's relaunch command> lane-7 ...` (it replaces only the agent ...)
alarms in the last 24h: 104 · explained 95 · real 8 · unknown 1 · lanes 21 · open now 0
explained by: declared-wait 43, torn-down 19, running-long 15, waiting-on-supervisor 9, queued 7, thinking 2
open now: none
real or unknown alarms in the window, newest first:
  - a086 2026-10-10 15:45Z lane-7 wedged: an instruction is unread after 3 doorbell attempts and ... | now running-long
  - a083 2026-10-10 15:43Z lane-4 wedged: an instruction is unread after 3 doorbell attempts and ... | later declared-wait (a091)
doctor: WARNING · 2 finding(s), 0 critical, 4 still watching · 30 checks in 3045 ms
  - [WARNING] update-restart:box: Windows Update restarted box 1 time(s) in the last 24h → Check that every lane ...
```

When lanes are open, an `open now` block names each one, with its newest alarm, what asking again said and the recommendation.
At most `advice.list_max` (20) real or unknown alarms are listed. `advice.json` has all of them.

**`advice.json`**: `infirmary.advice 1.0`.

| Key | Meaning |
| --- | --- |
| `written_at`, `at_epoch`, `host`, `home`, `adapter`, `state_dir`, `tool_version` | when, where, by what |
| `mode` | always `advice-only`; `live_repair` is the operator's toggle as recorded (an advice run never repairs either way) |
| `off` | the kill switch was on: nothing below was filled in |
| `card_tool` | the card's `tool` id |
| `alarms.available`, `alarms.why` | whether the source's alarms were read, and why not |
| `alarms.window_s`, `alarms.since` | the window |
| `alarms.summary` | `total`, `explained`, `real`, `unknown`, `by_explanation{verdict: n}` (most first), `lanes`, `open_lanes[]`, `left_unexplained` and `left_lanes` (what the wall budget cut: never counted as explained) |
| `alarms.rows[]` | every alarm: `id`, `at`, `subject`, `window`, `verdict`, `kind` (explained, real, unknown), `qualifiers`, `why`, `recommendation` |
| `alarms.flagged[]`, `alarms.flagged_more` | the real and unknown ones, newest first, each with `now` (asked again) or `later` (a later alarm of the same lane) |
| `alarms.judged` | the judged ledger's part of the run ([judged.md](judged.md)): `verdicts_appended`, `outcomes_appended`, `pending`, `counts` (the last 14 days), `problems[]`, `ms`; `null` when off or the alarms were not read, `persisted: false` under `--no-write` |
| `alarms.now{lane}` | `verdict`, `kind` and `why` from asking again, for lanes whose newest alarm was real or unknown |
| `doctor` | `status`, `checked_at`, `checks`, `took_ms`, `pending`, `findings[]` (`id`, `status`, `title`, `recommendation`), `incidents` (the `incidents` block of `doctor.json`: `rules[]`, `rows[]`, `open`, `unknown`, `appended[]`), or `error` when the check broke |
| `budget` | the run's cost and what it decided ([What a run costs](#what-a-run-costs-and-when-it-steps-aside)): `decision` (`full`, `shortened`, `skipped`), `reason`, `wall_s`, `limits`, `load`, `power`, `cpu_s_last_hour`, `backed_off_runs`, `took_s`, `cpu_s`, `cut`, `notes[]`. A skipped run has `doctor` and `alarms` null |
| `took_ms` | the whole run |

**`card.json`**: one [`lookout.card` 1.0](findings.md#status-card-optional) card.

```json
{"schema": "lookout.card", "schema_version": "1.0", "tool": "stall-explain-box", "title": "Alarms explained · box",
 "status": "ok", "headline": "24h: 95 of 104 alarms explained (declared-wait 43, torn-down 19, running-long 15, …); 8 real, 1 unknown; 0 lanes open now",
 "numbers": [{"label": "alarms", "value": 104}, {"label": "explained", "value": 95}, {"label": "real", "value": 8},
             {"label": "unknown", "value": 1}, {"label": "open now", "value": 0}, {"label": "top explanation", "value": "declared-wait 43"}],
 "updated_at": "2026-10-10T17:36:17Z", "mode": "advice-only", "stale_after_s": 1800}
```

`status` is `warn` while any lane is open, `ok` otherwise, and `unknown` when the alarms could not be read or the kill switch is
on, and `unknown` with `skipped on purpose: <reason>` when the run stepped aside. While an incident is open the card says so first: the headline starts `N incidents open (kinds)`, `incidents open` is the first number
(a card holds six, so the last one, the top explanation, makes room), and the status is `warn`, or `bad` while a critical incident is open. Infirmary checks that could not decide are said too: the headline starts `doctor: N unknown, first <check>: <reason>`, `unknown checks` is the first number, an otherwise `ok` card becomes `unknown`, and `advice.txt` lists each as `[UNKNOWN, not a pass]` with its class, reason and next step ([the vocabulary](findings.md#the-unknown-vocabulary)). `tool` is `stall-explain`, or `stall-explain-<label>` with `--label`, so the cards of several homes can share one board (a board
keeps one card per tool). The card counts as current for three advice intervals and never less than 15 minutes. To put it on a
board on the same machine, install with `--card <the board's drop directory>`. From another machine, copy the file:
`cat <dir>/card.json | lookout card add -` on the board's host.

## The install

```
infirmary install --home ~/my-home --adapter my-adapter --advice-only --label box
```

- **One entry, in the user's own scheduler.** On Linux and WSL it is one line in the user's crontab, every 10 minutes
  (`--every` 1 to 60, a divisor of an hour). The line ends in `# infirmary advice <dir>`. Every other line is kept byte for
  byte, and the crontab as it was is saved in `<dir>/crontab.before`. On macOS it is one LaunchAgent,
  `~/Library/LaunchAgents/org.infirmary.advice.<hash>.plist`, loaded with `launchctl bootstrap gui/<uid>`. `--scheduler cron|launchd`
  overrides the choice. Nothing is system-wide, no service or daemon is restarted, no shared configuration is edited, and nothing
  asks for `sudo`. A second install replaces the entry; it never doubles it.
- **On a Mac the agent carries its own environment** (see below): a `PATH` and `ProcessType` `Standard`. Reinstalling replaces an older
  agent that has neither.
- **Advice-only, and nothing else.** `--advice-only` is required, and it is the only kind of install there is. The install writes
  no live-repair record, refuses `mode=repair`, and schedules `advise`, which never repairs. Turning live repair on stays the
  operator's own `infirmary live on --config <dir>/infirmary.json`, and even then the scheduled run does not repair.
- **What it writes.** `<dir>/infirmary.json` holds the configuration the entry runs with: the adapter, the home, the state
  directory, the `advice` settings, plus anything from `--config FILE` and `--set`. It must load, or nothing is scheduled.
  `<dir>/install.json` records what was installed, by whom and when. The first advice runs at once, so the files exist before
  the first scheduled run. Each scheduled run overwrites `<dir>/advise.log` with its own output, which is empty on success.
- **Check it.** `infirmary installed --home HOME` says whether the entry is in place, how old the advice is (fresh means newer
  than three intervals), what it found, whether live repair is on, and what the last run printed. It exits 0 only when the
  entry is present and the advice fresh.
- **Undo it.** `infirmary uninstall --home HOME` removes the entry (the crontab returns to what it was, with any lines added
  since kept) and the directory's own files. Any file the install did not write is left in place and named. `--keep-files` keeps
  the advice and the Infirmary's state; `--dry-run` says what would go. Uninstall finds the install by its record, so it needs no
  other option.
- **Where it runs from.** The entry runs this checkout's `bin/infirmary` with the Python that ran the install. Keep the
  checkout where it is, or install again after moving it.

### Scheduled runs on a Mac

A LaunchAgent starts a job with almost no environment, and the first install on a Mac showed what that costs. The plist the install writes now
carries both fixes; a reinstall (the same `install` command again) replaces an older agent that has neither.

- **`EnvironmentVariables` / `PATH`.** launchd gives an agent only `/usr/bin:/bin`, so a tool in Homebrew or `~/.local/bin` and
  `sysctl` (`/usr/sbin`) are not found. The plist sets `PATH` to `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin` (written out as the
  installing user's own home at install time; the repository holds no home path), `/usr/bin`, `/bin`, `/usr/sbin` and `/sbin`. Without
  `sysctl` the machine's boot time cannot be read, and a status line or busy hook counts only when it came after the last boot, so a rule that
  needs it says **unknown, with that reason** for the lanes it cannot judge. It never counts them as gone (the first run read six lanes gone
  where three were).
- **`ProcessType` `Standard`.** `Background` tells launchd to throttle the agent's CPU and I/O, and a run that takes half a second by hand then
  overran the Infirmary's tick budget (`doctor.tick_budget_ms`, 2 s) and left a rule's reads `unknown`. The key is `Standard`, never `Background`. If a scheduled run is still much
  slower than the same command by hand, `infirmary installed --home HOME` shows the last log and `doctor.json` `cost.took_ms` shows the run.
  The plist keeps `LowPriorityIO`, a disk-only hint; it was not the cause.
- **The tick budget stays honest.** A run that goes over the tick budget still says so (`cost.over_budget`).
- **On Linux and WSL** cron also starts a job with a short `PATH` (`/usr/bin:/bin`): put a `PATH=` line above the installed line in the
  crontab if the backend's tool lives elsewhere. The install does not edit other lines.

### Platforms

| | Linux / WSL2 | macOS | Windows |
| --- | --- | --- | --- |
| `advise` | **run** (synthetic home; read-only with `--no-write` on a live home) | same code, not run | same code, not run |
| install, installed, uninstall | **run** against a `crontab` shim | **run** against a `launchctl` shim on Linux only; **not run on a Mac** | not supported (no scheduler entry); run `advise` from the Task Scheduler by hand |
