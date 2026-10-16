# Verdicts, rules and thresholds

Schema: `infirmary.verdict 1.0` (separate from the tool version). One verdict is one JSON object:

```json
{"schema": "infirmary.verdict", "schema_version": "1.0", "name": "stall-explain", "tool_version": "0.1.0",
 "checked_at": "2026-10-08T21:40:15Z", "status": "OK", "verdict": "running-long", "qualifiers": [],
 "subject": "lane-build", "why": "build job (cargo build) for 25m (p50 28m, p90 40m, n=31)",
 "evidence": [{"source": "slot holder file", "fact": "build slot held 25m on this host: cargo build --release"}],
 "recommendation": "no action; recheck if it runs past its usual length", "exit_code": 0, "budget_ms": 1000, "took_ms": 6}
```

`status` follows a watcher's guard contract: `OK` (explained, nothing to do), `WARNING` (explained, a person
must act), `CRITICAL` (a real stall), `UNKNOWN`. `recommendation` is text for a person, never a command.

## The rule order

The first rule with **positive evidence** wins. `facts` that were seen go into `evidence`.

1. **dead**: the recorded agent process is positively not there (pid and start time and command line must match what was recorded; a pid alone is never enough).
2. **waiting-on-human**: the trigger or the agent state says the agent is blocked on a prompt.
3. **torn-down**: the lane has no live record, the agent is not known to be busy and no job is held. Positive evidence is the absent lane record, as of the alarm's moment: a source that cannot reconstruct the lane map at that moment says `known: false`, and the answer is `unknown` with the reason, never `torn-down`.
4. **running-remote / running-long**: a job held by the lane. Past the class cap (`job_max_s`) the record is ignored as stale (`stale-record`). Past `wedge_p90_factor` times the command's p90 (needs `p90_min_n` samples, else the line says `n<15`) with no growth, it is ignored too. A job on a host is `running-remote` when that host's beat is fresh and its canary is ok, otherwise `running-long [host-unverified]`; a host beat at `warn` or `critical` adds `slow-host`.
5. **queued**: a ticket (a place in line), or a pipeline run waiting its turn.
6. **running-long**: a pipeline step round or agent invocation in progress; the process tree above `busy_cpu_cores`; watched files changed within `activity_fresh_s`.
7. **thinking**: the agent is mid-turn per its own state. `busy-attested` is added when the only evidence is the watcher's record that a doorbell was deferred because the agent was busy (no activity sample).
8. **wedged** (unread instruction): the trigger says an instruction stayed unread after repeated attempts with an idle agent, or that the watcher could not type the doorbell because the agent exited or its endpoint is gone (`doorbell-undelivered`), and nothing in steps 4 to 6.
9. **waiting-on-supervisor**: the worker's newest declaration is `done`, `failed`, `blocked` or `needs-decision`, no later `resolved` closed it, and it is newer than the machine's last boot and than the lane's newest agent session (a source that dates them; otherwise the declaration does not count, `[stale-declaration]`, and the rules below decide from what is left, never benign).
10. **declared-wait**: the newest declaration is `paused`, and it is newer than the machine's last boot and than the lane's newest agent session (the same floor as step 9). If it states a length ("about 1-2h" or "until 2026-10-08T21:30Z") and `declared_wait_grace_factor` times that has passed, the answer is `unknown` (overdue).
11. **wedged / unknown from silence**: with an idle time known (from the trigger or `--idle-s`): `wedged` when idle at least `wedge_min_s` and nothing running, queued, growing or declared; otherwise `unknown`, "quiet N, below the wedge floor". A source that failed or timed out turns `wedged` into `unknown` ("evidence incomplete"): silence plus a broken reader proves nothing.

## Thresholds (all in `config.py` DEFAULTS, all `--set`-able)

| Name | Default | What it is |
| --- | ---: | --- |
| `budget_ms` / `hard_cap_ms` | 1000 / 2000 | target and hard limit per verdict; over the cap the source marks a timeout and the verdict degrades to `unknown` |
| `thresholds.wedge_min_s` | 900 | silence floor before `wedged` |
| `thresholds.wedge_p90_factor`, `p90_min_n` | 1.5, 15 | a job is suspect past this multiple of its command's p90, and p90 is only quoted with this many samples |
| `thresholds.busy_cpu_cores`, `idle_cpu_cores` | 0.2, 0.01 | process-tree CPU that counts as working or idle |
| `thresholds.activity_fresh_s` | 120 | a log or ledger that grew this recently is progress |
| `thresholds.declared_wait_grace_factor` | 1.5 | a declared wait is overdue at this multiple of its stated length |
| `thresholds.job_max_s` | build 3600, farm 7200, floor 3600, browser 3600, default 7200 | holder records older than this are not evidence |
| `thresholds.beat_fresh_s`, `canary_fresh_s` | 60, 900 | how fresh a host's beat and canary must be to call a remote job healthy |
| `thresholds.cpu_sample_ms` | 400 | gap between the two CPU samples |

These are a first cut taken from the design's numbers; tune them from your own replays.

## Exit codes

0 benign (including `torn-down`), 10 `waiting-on-supervisor`, 20 `waiting-on-human`, 30 `wedged`,
40 `dead`, 50 `unknown`; 2 usage error; 3 internal error. With `--replay --gate`: 0 gate passed, 1 failed.
When the kill switch is off the command prints that and exits 0.
