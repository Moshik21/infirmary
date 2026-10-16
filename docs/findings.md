# Findings, `doctor.json` and the Lookout feed

## A finding: `infirmary.finding 1.2`

```json
{"schema": "infirmary.finding", "schema_version": "1.2", "name": "infirmary", "check": "stale-gate-refs", "catalogue": 4,
 "id": "gate-diverged:lane-7", "kind": "gate-diverged", "status": "WARNING", "host": "box", "subject": "lane-7",
 "title": "the review gate holds work the lane does not", "why": "gate 1111aaaa is not an ancestor of lane 2222bbbb ...",
 "evidence": [{"source": "git rev-parse (lane)", "fact": "refs/heads/fm/lane-7 -> 2222bbb...", "ref": "/path/to/worktree"}],
 "how": {"owner": "the gate tool", "note": "...", "dry_run": ["gate-tool", "sync", "--check"], "cwd": "/path/to/worktree", "dry_run_output": null},
 "hows": [], "recommendation": "Ask the lane's worker to take the gate's head into its branch ...", "retires_with": [],
 "pending": false, "first_seen": 1791500000, "age_s": 640, "qualifiers": [], "tier": "T0", "lookout_kind": "service",
 "target": {"lane": "lane-7", "worktree": "/path/to/worktree", "branch": "fm/lane-7", "gate_head": "1111aaaa...", "lane_head": "2222bbbb..."}, "card": null}
```

`status` is `WARNING`, `CRITICAL` or `UNKNOWN` (the Jev guard vocabulary; an `OK` is a check result, not a finding).
`how.dry_run` is an argv array shown to a person; it is **run only with `--dry-runs`** (or `doctor.dry_runs`), by the
Infirmary, once per distinct command, with a timeout, and the end of its output is attached as `dry_run_output`. The
argv comes from the check's own code, never from configuration. `recommendation` is text, never a command. `target`
(added in 1.1, may be `null`) is what a repair would act on, as the check saw it: a process tree's pids with their start
times and command hashes, a path, a lane's heads. A repair compares it with what it reads again just before acting. `card` (added in 1.2, `null`
for most findings) is the **incident card** of a failure that has actually happened (docs/incidents.md): `{"what", "since", "since_basis",
"blocking", "work_at_risk": {"state", "why"}, "records": [{"record", "fact", "at"}], "next_step", "unknown"}`. A confirmed finding that carries
one becomes one append-only row in `incidents.jsonl`; `unknown` is the reason when the rule could not read its input.

## `doctor.json`: `infirmary.doctor 1.7`

Written atomically to `<state dir>/doctor.json` (or `doctor.out`) on every `check`:

| Key | Meaning |
| --- | --- |
| `status`, `exit_code` | the worst result: `OK` 0, `WARNING` 10, `CRITICAL` 30, `UNKNOWN` 50 (a check could not decide and nothing worse was found) |
| `checks[]` | one row per check: `state` (`ok`, `finding`, `unknown`, `skipped`, `error`), `detail`, `took_ms`, and (1.6) `info[]`: lines a check shows beside its findings that are no finding (a lane with its agent gone that has recorded a wait, naming it; empty for most checks), and (1.7) `reason_class`: `check-broke` or `overran` for a check in `error`, `null` otherwise |
| `findings[]` | every finding, confirmed or `pending` (still inside its `for` duration) |
| `unknowns[]` | (1.7) every distinct unknown of this tick, each with its exact reason: see [the unknown vocabulary](#the-unknown-vocabulary). An empty list means every check decided or said it does not apply |
| `flags{host}` | `drain`, `hung`, `disk_critical`, `capability_drift`: advisory per-host flags for a router or a pool (`drain` is also set by a critical `tmpfs-full`, and by virtualization off in firmware or a hypervisor that failed at this boot). `true` / `false` are set only when the check ran; **`null` means not checked**. `capability_drift` is `false` only for a host that declares `expect` and matched it, `true` on drift |
| `mode`, `repair_mode`, `live_repair` | (1.1) `repair_mode` is `advice-only` or `live-repair`, the mode this tick ran in (`mode` keeps the older `advise` / `repair`); `live_repair` is the operator's toggle as recorded in the state directory: `enabled`, `by`, `at`, `why`. A dashboard card reads `repair_mode` |
| `windows{host}` | (1.2) what the Windows-side checks saw per Windows host: `wsl` (`vm_running`, `vmmem_mb`, `cap_mb`, `cap_source`, `cap_pct`, `vm_cpu_pct`, `service`), `leaks` (`scanned`, `parentless_listed`, `reported`, `reported_mb`, `left_alone`, `orphan_sessions`, `pids`) and `capabilities` (`login_shell`, `login_profile_ok`, `powershell`, `gpu[]`, `gpu_preference`, `tools{}`, `desktop_sessions`), each with the `probe` version. Absent hosts and checks that could not read a host leave their part out. (1.4) The platform checks (catalogue 23 to 27) add, per Windows side (a host in `doctor.hosts`, or this machine's own Windows side under this host's id): `platform` (`seen_from` `local` or `ssh`, `now_epoch` and `boot_epoch` on that side's clock, `tz_offset_min`, `wsl_expected` and why), `virtualization` (`hypervisor_present`, `firmware_enabled`, `vbs_status`, `vm_platform`, `wsl2_distributions`), `hypervisor` (`launch_failures[]`, `last_start`, `vsm_failures`), **`reboot`: `{pending, due_epoch, source}`, the shape a Harbor `harbor.beat/1` record carries** (see [windows-platform.md](windows-platform.md#hand-off-to-harbor-pending-restart)), `reboot_detail` (`sources`, `weak`, `staged_driver_updates`, `active_hours`), `restarts` (`windows_update[]` with `inside_active_hours`, `driver_updates`, `hypervisor_failed_after`; `unexpected[]`; `other[]`) and `update_policy` |
| `local{check}` | (1.3) what the checks of this machine saw beyond their findings, keyed by check id: `wsl-interop` (`registered`, `state`), `scratch-leaks` (`groups{label}` with `entries`, `leaked`, `leaked_bytes`, `in_use`, `young`, `sized_all`, `oldest_leaked_age_s`; `tmpfs{mount}` with `total_mb`, `used_pct`) and `ssh-check` (`aliases{alias}` with `state` of `ok`, `check`, `auth`, `unreachable`, `failed` or `missing`, and `url_seen`; `not_tried`, the aliases over the cap or not plain; the URL itself is only in the finding's evidence). A check that could not read its part leaves it out |
| `catalogue_hash`, `tool_version` | the Infirmary's own version, so a mismatch across hosts is visible |
| `cost` | `took_ms`, `local_ms`, the budget, `over_budget`, `rss_mb`, (1.1) `repairs_ms`, the time the repairs of the tick took, and (1.5) `incidents_ms`, the time the incident rows took to make and append |
| `incidents` | (1.5) the incident-card block ([incidents.md](incidents.md)): `enabled`, `rules[]` (each rule of the family that ran, with its `state`, `detail` and, when it has any, `info` lines: a skipped rule is listed too, and `advice.txt` gives its reason and the key to set), `rows[]` (every open or unknown incident, as the ledger row), `open`, `unknown`, `appended[]` (ids written to the ledger this run), `already[]`, `omitted`, `ledger`, `snapshot`, `problems[]`, `persisted` (false under `--no-write`), and `not_read`, present only when the configured adapter could not be loaded (its reason, for example `adapter X not found`; `advice.txt` then says `incidents: not read: <reason>`) |
| `repairs` | (1.1) `mode`, `outcomes[]` (one per action this tick: `action`, `tier`, `outcome`, `reason`, `findings`, `argv`), the open `plans[]` and each action's `breakers` state; see [repairs.md](repairs.md) |

`status` and `exit_code` are the detection result of the tick, taken before any repair ran; a repair's result is in
`repairs` and shows up as a cleared finding on the next check.

## Lookout feed

`infirmary check --push` (with `lookout.enabled`) sends the findings to a Lookout host as a source named
`doctor.lookout_source_id` (default `infirmary`), labelled `Infirmary · advice-only` or `Infirmary · live-repair` so the board shows which mode the Infirmary is in:

- each confirmed finding is an `attention[]` card: `critical` for `CRITICAL`, `action` for `WARNING`, `info` for `UNKNOWN`;
  `kind` is `machine`, `service`, `disk` or `capacity`; `how` carries the owner's dry run and what to do next;
- every distinct unknown of a check that has no finding of its own is one `info` card, `unknown:<check>:<class>`, titled `<check>: unknown (<class>)`, whose `detail` is the exact reason and whose `how` is the next step; the same card on every tick, never a second one for the same unknown (a finding that is itself UNKNOWN is its own card, and a rule's `incident-unreadable` finding is this card, not a second);
- each check is a `collectors[]` entry, so a check that errored shows as unavailable on the board instead of silently empty (its `error` starts with the class); a skipped check carries the reason in `warnings`, so a quiet row is never mistaken for a clean one; the board takes 20 collectors and rejects more, so with more than 20 checks the last ones share one collector `more-checks`, `error` if any of them errored, with their ids in `warnings`;
- a host flagged `drain` or `hung` is reported through `machines[].health` (`draining` or `lost`), and the host adds its own card;
- a finding that carries an incident card is sent with the card's plain words as its `detail` (what happened, what it blocks, whether work is at
  risk), the card's onset as `since`, and the one safe next step as `how`;
- a `pending` finding is not sent (an unknown check is sent at once: it has no `for` duration);
- every repair run and refusal of the tick is a card of its own (`repaired: ...` as `info`; `repair refused`, `repair failed`,
  `repair did not clear` as `action`), and every plan waiting for a person is a `decision` card whose `how` is
  `infirmary approve <id>`.

The token comes from a file or an environment variable, never argv. A host that is down changes nothing else.

## Status card (optional)

A dashboard that reads small status cards (`lookout.card` 1.0, one JSON file per tool in a drop directory) can show the Infirmary as
one tile. Off unless asked; the Infirmary imports no dashboard and the dashboard runs nothing of the Infirmary.

```
infirmary check --card /path/to/cards        also write the card after the check (a directory: <dir>/infirmary.json)
infirmary card                               print the card for the last check (reads doctor.json; runs no check)
infirmary card --card /path/to/cards         write it instead of printing
infirmary card | lookout card add -          validate, then drop it into the board's directory
```

`status` is `ok` / `warn` / `bad` / `unknown` for `OK` / `WARNING` / `CRITICAL` / `UNKNOWN` (and `unknown` while the kill switch is
on); `headline` counts the findings and the plans waiting for a person, and **never says `all clear` beside an unknown**: it adds `N unknown` with the
first check's name, class and exact reason; `numbers` are findings, critical, warnings, unknown and plans
waiting; `updated_at` is the check's own time; **`mode` is `advice-only` or `live-repair`**, read from the home's toggle when the
card is made, so the tile never says live repair for a home that is not. `stale_after_s` is 900 unless `--stale-after SECONDS`
says otherwise (set it to a few times the interval you run the check at). `card` exits 1 and prints nothing to stdout when there
has been no check yet: no card, not an empty one. The write is a temporary file renamed into place.

## The unknown vocabulary

An unknown is the Infirmary saying it could not decide. It is never a pass and never blank: every surface says it, with the exact reason, from
one list (`doctor.json` `unknowns[]`). A check that does not apply here, or is switched off, is `skipped` with its reason; that is not an unknown.

| `class` | when | the reason is | `id` |
| --- | --- | --- | --- |
| `could-not-read` | the check answered `unknown`: an input was missing or unreadable, or what it read did not decide | the check's own words (the file or call, and why) | `unknown:<check>:could-not-read` |
| `check-broke` | the check raised an error | the error's type and message | `unknown:<check>:check-broke` |
| `overran` | a probe of another host did not finish within `doctor.remote_budget_ms` | the budget that was used up | `unknown:<check>:overran` |
| `rule-unknown` | a finding whose own status is UNKNOWN (a host whose probe failed, a machine whose feed is last-known) | the finding's reason | the finding's id |

Each entry is `{"id", "check", "title", "class", "reason", "next", "host"}`; `next` is advice as text, never run. One unknown is one card (the id does
not change between ticks), two checks that say the same words are two cards, and a reason is never empty (`the check gave no reason` is said when a
check gives none). The count is in the card's `numbers`, in `check`'s footer and in `advice.txt`; the cost is one pass over the document the check already built.

## Alarm card (from `advise`)

`infirmary advise` writes a second card, `tool` `stall-explain` (or `stall-explain-<label>`): how many of the alarms recorded in
the last 24 h were explained, the top explanations, how many were real or unknown, and how many lanes are still open. It is
`card.json` in the advice directory, and also goes to `advice.card` when that is set. Its fields and its `status` rule are in
[advice.md](advice.md#the-files).
