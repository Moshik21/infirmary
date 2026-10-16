# Judged alarms: what the explanations came to

No tool earns its place unmeasured. For every stall alarm the Infirmary explains, `infirmary advise` records one **verdict row**: what it said
and what it rested on. Once enough time has passed, a deterministic follow-up records one **outcome row**: what the lane did next, and whether
the verdict was wrong. `infirmary judged` counts the rows, so the number of wrong-benign verdicts is a number you read, not a story.

Advice-only, like everything else here: nothing acts on a row, and a row is not a repair, a card or a feed. The words are the records' own
(explained, called real, relaunched, finished, moved). It claims no benefit: no record shows that a person acted because of the tool, or what
would have happened without it, so nothing here counts a stall as stopped or time as gained.

```
infirmary judged [--days N] [--json]    # counts for the last N days (default 14), from judged.jsonl only
```

## The ledger

`<state>/judged.jsonl`, beside `incidents.jsonl`: append-only, one JSON line per row, written one whole line at a time under a file lock, never
rotated, trimmed or edited (at 16 MiB, about 145 days of 100 alarms a day, it refuses new rows and says so in `advice.json` and `advice.txt`;
moving the file away is the owner's decision, not the tool's). `uninstall` leaves it in place and names it. It is a source for the value ledger's
counters, in the same family: `v`, `id`, `tool`, `host`, `read_at`, `reader`, `supersedes` (always `null` here: nothing is corrected, an outcome is a
new row), and an unknown written as unknown with its reason. A reader skips a line that does not parse or repeats an id, and lists it.

**`verdict`**, appended once per alarm, the first time an advise run explains it (the id is `verdict:<alarm time>#<text hash>`, the hash
being the first 8 hex digits of the sha1 of the alarm's text, which names the window, so a later run that explains the same alarm finds the id and
writes nothing). The id is built from the alarm's content, not its position in the watcher's log nor the lane it resolves to: a delivery log rotated
or trimmed from the head renumbers the alarms, and a lane cleaned up or a window reused changes the lane an alarm resolves to, and each alarm still
has the id it had, so it is never recorded twice. The counts go by id, one verdict per id and one outcome per verdict. Identical repeat deliveries
(the same alarm text at the same moment, so for one window) are one verdict row, so the judged alarm count can be lower than `advice.json`'s. The verdict is the one given then: what the records said at the alarm's moment.

| Key | Meaning |
| --- | --- |
| `alarm` | `id` (the alarm's number in the watcher's log when the row was written: for reading only, the row's id does not use it), `at` (when it was delivered) and `text_sha1` (the hash in the id; the text itself is not kept) |
| `subject` | the lane |
| `verdict`, `kind`, `claim`, `qualifiers` | the verdict; `explained`, `real` or `unknown` ([advice.md](advice.md)); what it claims about the lane (`moving`, `owed`, `gone`, `human`, `real`, or `null` for `unknown`); its qualifiers |
| `evidence_class` | the sources whose facts the verdict cites (`declarations`, `lane map`, `slot log`, `trigger`, ...), with no instance and no age |
| `state_ages` | how old each state source was at the alarm's moment: `declaration`, `session`, `boot`, `slot_record`, `pipeline`, each `{"age_s": n, "unknown": null}` or `{"age_s": null, "unknown": "<why>"}`; never `0` for a source that gave no time |
| `settle_s`, `patience_s` | the window the lane is judged over, and how long a quiet lane may wait for a later alarm (below) |

**`outcome`**, appended once per verdict, once `settle_s` (30 minutes) has passed since the alarm. The id is `outcome:<verdict id>`, `of` names the
verdict row.

| Key | Meaning |
| --- | --- |
| `outcome` | what the lane did next (below), or `unreadable`, or `none` (the verdict was `unknown`) |
| `judgement` | `agreed`, `wrong-benign`, `wrong-real` or `undecided`, from the table below |
| `reason` | why: for `undecided`, the unknown's reason |
| `records` | the records inside the window that name the outcome (`record`, `kind`, `at`), at most six |
| `instructed` | an instruction was delivered to the lane inside the window |
| `window_s` | the window that was used |

## The follow-up: what the lane did next

It runs at the end of each advise run, on the verdict rows that have no outcome row yet, and reads what the run already read: the lane's status
log, the terminal outcomes, the lane map and the entry kept from before a relaunch, the fleet ledger and the slot log (an adapter's
`outcome`, [sources.md](sources.md)), plus the names and times of the files in the lane's own inbox. No process is started. It looks only at
records stamped inside the window after the alarm, `(alarm, alarm + settle_s]`, so running late changes nothing, and it names the **strongest** record
inside the window:

| Outcome | The records show, inside the window |
| --- | --- |
| `relaunched` | a newer agent session of the lane, or the lane dispatched again |
| `gone` | the lane cleaned up |
| `finished` | the lane's `done` line, or a terminal outcome |
| `answered` | a `resolved` line |
| `moved` | any other line of the lane's own, or a slot job of its worktree starting or ending |
| `quiet` | nothing at all, with the lane's records readable |
| `stuck` | `quiet`, and a **later real alarm** for the same lane (at least `settle_s` after this one, at most `patience_s`, 6 hours) with nothing recorded in between |

A lane that recorded nothing is not called stuck for its silence: a long job, a declared wait and a model call are all quiet by design. It waits,
as pending, for a later alarm of its own, and when `patience_s` has passed without one it is `undecided`. A lane whose records cannot be read
(a window no lane record names, a status log that fails to open, a lane with no record and no ledger entry) is `unreadable` and `undecided`, with
the reason.

## The wrong rule

A fixed table from (what the verdict claimed, what the lane did next) to a judgement. `queued`, `running-long`, `running-remote`, `thinking` and
`declared-wait` claim the lane is **moving** on its own; `waiting-on-supervisor` that the next move is **owed** by the supervisor; `torn-down` that the
lane is **gone**; `waiting-on-human` that a **human** has to answer; `wedged` and `dead` that the lane is **really** stuck. `unknown` claims nothing:
it is always `undecided`.

| What the lane did next | moving | owed | gone | human | really stuck |
| --- | --- | --- | --- | --- | --- |
| `relaunched` | **wrong-benign** | undecided | undecided | agreed | agreed |
| `gone` | undecided | undecided | undecided | undecided | undecided |
| `finished` | agreed | agreed | undecided | undecided | **wrong-real**\* |
| `answered` | agreed | agreed | undecided | undecided | **wrong-real**\* |
| `moved` | agreed | undecided | undecided | undecided | **wrong-real**\* |
| `stuck` | **wrong-benign** | undecided | undecided | agreed | agreed |
| `quiet` | undecided | undecided | agreed | undecided | undecided |
| `unreadable` | undecided | undecided | undecided | undecided | undecided |

\* `undecided` when an instruction reached the lane inside the window: the records then cannot show it moved on by itself.

In words:

- **wrong-benign**: a benign verdict followed by a stall that persisted past the window (`stuck`) or that needed a relaunch (`relaunched`).
- **wrong-real**: a real verdict (`wedged` or `dead`) where the lane resolved by itself inside the window: it moved on, with no relaunch and no
  instruction recorded.
- **undecided**: anything not yet decidable or lacking evidence, with its reason: the window is still open, the lane was quiet, the records could
  not be read, the verdict was `unknown`, or the records fit more than one story. Never counted as agreed.
- **agreed**: the outcome fits the verdict, on a record that names it. It is not praise; `agreed` rows say only that nothing recorded contradicts them.

The table is the whole rule, in `src/infirmary/doctor/judged.py` as `TABLE`; a cell the table does not name is `undecided`. The reason in each row
is the table's own text.

What this cannot know, and says so:

- **A person acting at the pane leaves no record.** `wrong-real` means no record of anyone acting, so it is an upper bound on the verdicts that were
  really wrong; `waiting-on-human` is never `wrong-real` for that reason. A relaunch is the supervisor's act and is recorded.
- **Silence is not a stall.** Only a later real alarm, or a relaunch, makes a benign verdict wrong. A benign verdict about a lane that then stays
  quiet with no later alarm stays `undecided`, so the wrong-benign count is a floor: the undecided count says how much it cannot see.
- **It judges the verdict given then**, not what the rules say today. A rule that changed after the alarm does not rewrite old rows.
- **One lane, one alarm at a time.** A relaunch for another reason inside the window counts as a relaunch.

## The number a person reads

```
$ infirmary judged
judged alarms · 2026-09-26 20:20Z to 2026-10-10 20:20Z · advice-only: nothing acts on a row
  alarms recorded      6   verdicts in the window
  explained            3   called benign or the supervisor's move
  real                 3   called real
  unknown verdicts     0   the evidence did not decide
  agreed               1   what the lane did next fits the verdict
  wrong-benign         2   a benign verdict, then a relaunch or a stall that persisted past the window
  wrong-real           1   a real verdict, then the lane moved on with no relaunch and no instruction recorded
  undecided            2   not yet decidable or lacking evidence (never counted as agreed)
        1  an instruction reached the lane inside the window, so the records cannot show it moved on by itself
        1  the alarm's window is still open
the ledger starts at 2026-10-10 20:20Z, after the window does: these are counts from then on, at least that many, not the whole window
```

(The counts are from a synthetic home.) `--json` prints the same as `infirmary.judged.report`: `counts` (`alarms`, `explained`, `real`, `unknown`,
`agreed`, `wrong_benign`, `wrong_real`, `undecided`, `undecided_by{reason: n}`, `window`, `rows_start`, `lower_bound`), `skipped[]` and `unknown`.
`explained + real + unknown` is `alarms`, and `agreed + wrong_benign + wrong_real + undecided` is `alarms` too. A verdict with no outcome row yet is
`undecided`, and says why. With no ledger, or none in the window, the report says `unknown` with the reason, and the exit status is 1: no verdict
row is not zero wrong verdicts. When the ledger starts after the window does, the counts are marked as a lower bound. `advice.txt` carries the
14-day line, and `advice.json` the same under `alarms.judged`.

How a value ledger's counters read this file (`docs/ledger-spec.md` of the dashboard repository; the writer that does it belongs to the ledger's
owner, not to this tool): `explain_verdicts` is the `verdict` rows by `verdict`; `judged_real` is the outcome rows that are `agreed` for a verdict of
kind `real`; `judged_wrong` is the `wrong-benign` and `wrong-real` outcome rows. `src.file` is `judged.jsonl`, and `src.filter` names the `row` and
`judgement`. The file holds a lane name, which the counters do not carry: they hold counts only.

## In a replay

`stall-explain --replay FILE` judges its verdicts the same way when the replay's classes name what the lane did next (`"outcome"` in the header: the
hand label's word, `relaunched`, or `healthy` for a lane the hand label calls healthy), and prints one line, `judged against the hand-labelled
outcomes (offline)`, and `score.judged` in `--json`. Nothing is written to the ledger: a replay is not an alarm. A recorded restart
burst of the author's fleet was labelled this way; its replay file is kept with that fleet's adapter, outside this repository. Replayed as it stands, no verdict is wrong; replayed with evidence that carries neither the boot nor the session time
(the engine then judges a declaration as recorded, which is how it stood before that burst), nine stalled lanes are called a `declared-wait` and
were then relaunched: nine wrong-benign verdicts, while the three healthy look-alikes stay agreed. That replay is not run by this repository's `scripts/test`.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `advice.judged.enabled` | `true` | `false`: no row is appended (the alarms are still explained) |
| `advice.judged.settle_s` | 1800 | the window after an alarm in which the lane's records say what it did next |
| `advice.judged.patience_s` | 21600 | how long a quiet lane waits for a later real alarm before it is `undecided` |
| `advice.judged.ledger_max_kb` | 16384 | at this size the ledger refuses new rows and says so |

The numbers are proposals, not measurements; each is a setting, so the first fortnight's reading can move them without changing the rule. A row
written under one `settle_s` keeps it (`settle_s` is in the verdict row).

## Cost

It reads what an advise run has already read and appends a few short rows; no process is added, nothing new is polled, and the one new read is the
names and times of the files in a lane's own inbox. A verdict row is about 0.7 KB and an outcome row about 0.4 KB, so 100 alarms a day is about 110 KB
a day. On the fixture's synthetic home (21 lanes, 105 alarms in the window): the first run, which appends 105 verdict rows and 105 outcome rows, took
2 ms, and every later run 1 ms (the ledger read once, and one pass over it), inside a whole advise run of about 20 ms. The whole ledger is read once
per run, about 11 ms for each MiB, so a fortnight of it (1.5 MB) costs about 16 ms and the full 16 MiB about 0.2 s. `advice.json` reports the run's
own `alarms.judged.ms`.

A source that records no alarms (the built-in `files` source) has no verdicts to judge and writes nothing. A source that records alarms and has no
`outcome` gets, for each verdict, an outcome row that says `the source cannot say what a lane did next`.
