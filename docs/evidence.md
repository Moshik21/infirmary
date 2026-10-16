# Evidence: `infirmary.evidence 1.0`

The engine is a pure function of one evidence dict. A source fills it from local files and the process table (`/proc`, `ps` on
macOS, `Win32_Process` on Windows; [verdicts.md](verdicts.md#process-evidence-by-platform)) within the time budget; a replay file embeds it. Every key is optional except `subject`.

| Key | Shape | Meaning |
| --- | --- | --- |
| `subject` | `{id, label}` | the lane |
| `as_of` | epoch seconds | the moment the evidence describes (a replay uses the alarm time) |
| `lane` | `{known, closed, note, session_at}` | `closed` = no live record for the lane. `known: false` with a `note` = the lane, or its record as of `as_of`, cannot be reconstructed: the verdict is `unknown` and says why. `session_at` (epoch seconds, or null when unknown) = when the lane's newest agent session began |
| `host` | `{boot_at}` | the machine's last boot as of `as_of` (epoch seconds, or null when no source names it) |
| `trigger` | `{kind, idle_s, escalation, attempts, text}` | why the question was asked. Kinds: `silence`, `wedge-timer`, `doorbell-busy`, `doorbell-idle`, `doorbell-undelivered` (an instruction is unread and the watcher could not type the doorbell because the agent exited or its endpoint is gone), `agent-blocked` |
| `declarations` | `[{at, state, key, text, wait_hint_s, until}]` | what the worker said about itself ([grammar](sources.md#declaration-log)) |
| `agent` | `{state busy\|idle\|blocked\|unknown, source, alive, cpu_cores, descendants}` | `alive` is true/false only when process identity could be judged |
| `activity` | `{newest_age_s, grew, files, source}` | newest change under the watched globs |
| `jobs` | `[{kind holder\|ticket, class, host, tier, started, held_s, cmd, cmd_key, ahead, alive, source}]` | a slot held, or a place in line |
| `history` | `{cmd_key: {n, p50_s, p90_s}}` | durations of past runs of the same command, as of `as_of` |
| `hosts` | `{name: {beat_age_s, level, canary_ok, canary_age_s}}` | health of hosts jobs run on |
| `pipeline` | `{state round-active\|pending\|parked\|none, step, round, trigger, since, last_activity, source}` | a step ledger's view |
| `sources` | `[{name, status ok\|unavailable\|timeout\|error, ms}]` | which readers ran; a failed reader blocks conclusions from silence |

### A declaration must be newer than the boot and the session

A source that sets `host` or `lane.session_at` vouches that it can date the machine's last boot and the lane's newest agent
session as of `as_of`. The engine then counts a declaration of any kind (`paused`, `done`, `failed`, `blocked`, `needs-decision`,
`working`, `resumed`) only when it is newer than both: one written before a reboot or before a relaunch says nothing about the
agent that is there now, so it can never make the answer `declared-wait` or `waiting-on-supervisor`. The same floor applies to a
busy hook line read live. When a source sets the key but
cannot say the time (null), the declaration cannot be shown to be newer and does not count. Evidence that carries neither key
was recorded before this rule and is judged as recorded. A source must also resolve the lane as of `as_of`, not as of today:
what it cannot reconstruct is `known: false`, never `closed`.

## Replay file: `infirmary.replay 1.0`

JSON Lines. Line 1 is a header (`kind: header`, `title`, `classes`). `classes` maps a hand label to what
agrees with it: `{"expect": "flagged"}` for real stalls (any non-benign verdict), or
`{"expect": ["waiting-on-supervisor"], "benign": true}`. Every other line is `{kind: alarm, id, at,
class, scored, evidence}`. `scored: false` alarms are run and listed but excluded from the gate.
`class: null` means no hand label. A class may also say what the lane did next, `"outcome": "relaunched"` (or `"healthy"`), which makes `--replay`
judge the verdicts against it, offline ([judged.md](judged.md)).
