# Incident cards: one row for each failure that has actually happened

A snag the Infirmary finds is a *finding*. A **failure that has actually happened** (a lane's own log says it failed) is more than that: somebody has to be told in plain words what happened, since
when, what it is holding up, whether work is at risk, what the claim rests on and what to do first. That is an **incident card**, and each
confirmed one becomes exactly one **append-only row** in the incident ledger.

```
infirmary check            # the cards appear under their findings, and the rows are appended
infirmary advise           # the same, on a schedule: advice.txt lists the incidents first
```

Advice-only, like the rest of the Infirmary: nothing acts on a row, and nothing acts on its next step. The step is text for a person.

## The rule family

The core ships no incident rule: an adapter supplies them, each for a failure that has happened and each with a healthy look-alike that it
must leave alone. The example adapter has one, `lane-failed` (catalogue 91, [its README](../examples/adapter/files-home/README.md)). Every rule is
deterministic, reading task metadata, the process list, status folds, run records and logs. No model is called, and nothing here starts, stops,
repairs or deletes anything.

## What a row says

One JSON line (`infirmary.incident 1.0`), in the family of the value ledger: append-only, one line per record, an unknown shown as unknown
with its reason.

| Field | Meaning |
| --- | --- |
| `id` | `<kind>:<key>@<first seen>`: stable while the incident lasts, so a later tick that still sees it finds the id and writes nothing |
| `kind`, `check`, `catalogue`, `subject`, `host`, `severity` | what failed, which rule saw it, about what, on which machine, and how bad (`warning`, `critical`) |
| `status` | `open`, or `unknown` when the rule could not read its input (then `unknown` holds the reason) |
| `what` | what happened, in a sentence |
| `since`, `since_basis` | when, and from which record: the last sign of life, the beat, the oldest commit that never reached the remote. When no record dates the onset, `since` is when the Infirmary first saw it and the basis says so |
| `blocking` | `{"text", "unknown"}`: what it holds up. A card that cannot say has `text: null` and the reason in `unknown` |
| `work_at_risk` | `{"state": "yes" \| "no" \| "unknown", "why"}`: whether work exists only where the failure could lose it, as read (git status and unpushed commits, for a worktree) |
| `evidence[]` | `{"record", "fact", "age_s", "age_unknown"}`: each record the claim rests on and how old it is when read; a record that carries no time says so |
| `next_step` | the ONE safe next step, as text: the relaunch command that keeps the worktree, the steer to send, the abort that restores the branch. Never run by the Infirmary |
| `first_seen`, `read_at`, `reader`, `supersedes` | when the incident was first confirmed and when the row was read, by which tool version; `supersedes` is always `null` (a correction would be a new row naming the earlier id) |

A row records the incident **when it was first confirmed**. Nothing edits it later: whether it is still open is in the snapshot (below), and
the clearing of an incident is not recorded in v1 (the ledger holds starts, not ends).

## The files

All in the state directory.

- **`incidents.jsonl`**, the ledger. Opened for append and written one whole line at a time under a file lock; never truncated, rotated or
  edited; created by its first row (a run that finds nothing creates nothing). Only the tail (`doctor.incidents.tail_kb`, 512 KiB) is read to
  tell which ids it already holds. At `doctor.incidents.ledger_max_kb` (8 MiB) it **refuses new rows and says so** in the run's output instead
  of dropping old ones. `uninstall` leaves it in place and names it: it is a record, not derived state.
- **`incidents.json`** (`infirmary.incidents 1.0`), the snapshot of what is open now: the rules that ran and their states, every open or
  unknown row, how many were not carded (`omitted`), which ids this run appended, and which of its rows the ledger holds (`in_ledger`: an
  incident that stays open is never appended twice, however far its row has scrolled out of the tail). Rewritten atomically by each run that
  read every incident rule (a `check --only` of a subset appends to the ledger but leaves the snapshot as it was); the file a board or a
  supervisor reads. It is written only when the home's adapter offers incident rules.

Where else they show: each finding with a card prints `incident: blocking ... work at risk ...` and `next: ...` under `check`; `doctor.json`
(`infirmary.doctor 1.5`) carries an `incidents` block and `cost.incidents_ms`; `advice.txt` lists the incidents before the alarms, `advice.json`
holds them under `doctor.incidents`, and the card says `N incidents open` (status `warn`, or `bad` while a critical one is open); a Lookout feed
(`check --push`) sends each as an attention card whose detail is the plain words and whose `how` is the next step.

## Unknown is a row, never silence

A rule that cannot read what it needs does not go quiet:

- **an input that cannot be read** (the process table on this OS, the run ledger, a file that is not JSON) is an `unknown` row naming what could not be read (the file or the call, and why it was unreadable or absent), with the exact reason, and how to make it readable; an onset that no record dates says where it would have been recorded;
- **a lane that is not judged** is an `info` row `not examined: <reason>`, never silence;
- **a rule that breaks** (an exception, a timeout) is an unknown row too (`incident-unreadable:<check>`), with the error as its reason;
- **a rule with nothing to read** is `skipped` with the reason, and the snapshot lists it, so a missing rule is visible too.

An unreadable input waits out its `for` duration (120 s, `doctor.for_s.incident-unreadable`) before it becomes a row, so a flaky read is not
one. The incident rules themselves confirm at once: every one of them is dated by its own records, so the Infirmary does not need to have watched them for a while.

The wait is for the ledger row only. The status card, the board and `check` say the unknown at once, as one card per check and class with the exact reason
([the unknown vocabulary](findings.md#the-unknown-vocabulary)); the rule's `incident-unreadable` finding stands behind that card and is not sent to the board a second time.

## Latency and cost

The worst-case wait before a failure becomes a row is the rule's own threshold plus the run cadence. Run the incident rules every 2 minutes
instead of the default 10:

    infirmary install --home HOME --adapter NAME --advice-only --every 2

No other process is added. The rules run within the Infirmary's existing local budget (`doctor.tick_budget_ms`, 2 s); the fold into rows and
the ledger append are counted in `doctor.json` under `cost.incidents_ms`, and a row is about 1.7 KB, so the 8 MiB limit holds about 4,900 rows.
A rule that runs out of time reports what it did not read, with that reason.

## What it cannot know

- The ledger records starts, not ends, and a row's risk and blocking text are as of the first confirmation.

## Adding a rule

A rule is a check of the incident family ([CONTRIBUTING.md](../CONTRIBUTING.md#adding-an-incident-rule)): `registry.Spec(..., family="incident")`,
its findings built with `finding.card(...)`, its unreadable inputs answered with `registry.unknown(..., next_step=...)`, and a fixture pair: the
failure and a healthy look-alike that differs from it by one fact.
