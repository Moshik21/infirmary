# Accuracy: replay of one fleet's recorded alarms

*Measured 2026-10-08 with tool version 0.1.0, over one fleet's recorded alarms. The replay file (redacted evidence snapshots and hand labels) belongs to the adapter for that fleet's tool and is not part of this repository, so these numbers cannot be reproduced from a clone; the replay mode itself can be run on `src/infirmary/explain/synthetic.jsonl` (`stall-explain --replay src/infirmary/explain/synthetic.jsonl -v`) and on your own `--export-replay` output.*

## What was replayed

One supervisor home delivered **63 stale alarms** to its own supervisor in a day. Each alarm was joined
with the lane's own records as they stood at the alarm's moment: the lane's declaration log up to that
time, terminal-outcome records, the shared slot log (jobs in flight, queue arrival from `waited=`,
command durations from jobs finished by then) and the pipeline step ledger (rounds and agent
invocations whose time interval contains the alarm). The alarm text itself is evidence too (a deferred
doorbell, an agent reported blocked, an idle timer). Records that exist only as present state (a
host's canary now, a file's mtime now, the lane map) are not used for the past, except the lane map to
say a lane is gone.

The first **54** alarms were classified by hand by an independent scout: 2 real stalls, 18 owed to the
supervisor (11 finished, 7 blocked or asked a question), 9 declared waits, 8 live floor or fix rounds
where the watcher's idle timer fired, 11 doorbells deferred because the worker was busy, 6 for lanes
already torn down. Alarms 55 to 63 arrived afterwards; they are replayed but not scored (no independent
label). The hand labels and the redacted evidence snapshots are the fixture kept with that fleet's adapter, outside this repository.

## Result (the 54 scored alarms)

| Hand class | Alarms | Benign or supervisor-owed | Exact class | Verdicts given |
| --- | ---: | ---: | ---: | --- |
| real stall | 2 | flagged 2 of 2 | 2 | waiting-on-human 2 |
| owed to the supervisor | 18 | 18 | 18 | waiting-on-supervisor 18 |
| declared wait | 9 | 9 | 9 | declared-wait 3, running-long 6 |
| live floor or fix round | 8 | 3 | 2 | running-long 2, torn-down 1, unknown 5 |
| doorbell, worker busy | 11 | 10 | 10 | thinking 10 (all `busy-attested`), wedged 1 |
| lane torn down | 6 | 6 | 6 | torn-down 6 |
| **all but the real stalls** | **52** | **46 (88.5%)** | **45 (86.5%)** | gate: every real stall flagged and at least 80% of the rest benign or owed: **PASS** |

Unscored alarms 55 to 63: 8 of 9 benign or supervisor-owed; one `unknown`.

## The six misses, and what they mean

- **Five idle-timer alarms on a live lane (`unknown`).** The lane's last word was "working", the
  watcher's idle timer said about four minutes of silence, and no slot job, queue ticket, pipeline round
  or file growth was recorded for it, so the lane was either between steps or running something the records do not capture. The engine
  will not call that benign, and four minutes is below its 15-minute wedge floor, so the answer is
  `unknown`, "recheck in 11 minutes". That is the intended behaviour for missing evidence, and it still
  flags these five for a human look.
- **One doorbell with an idle pane (`wedged`).** An instruction stayed unread after three delivery
  attempts with the pane idle. The lane's one slot job had been held 20 minutes, past 1.5 times its
  command's p90 (12.6 minutes, 34 runs), with no growth evidence, so the engine did not count it as
  progress. The hand class said busy-deferred; the engine flags it because the doorbell was not
  reaching the lane. The
  scout noted the same alarm "deserved a look".
- Not counted as a miss because it is benign, but not the exact class: one live-run alarm on a lane whose
  record is gone now is called `torn-down` (the replay cannot tell the lane vanished after the alarm).

## Read these numbers carefully

- **Not held out.** The rules, the readers and the thresholds were written while looking at this same
  day's data. The result describes how well the design explains these alarms, not how it will do on
  tomorrow's. The real test is the two-week measured run (E2): verdicts against the supervisor's eventual
  findings, with the gate "no real stall classified benign". The judged ledger ([judged.md](judged.md)) is what records it: each explained alarm, what the lane did next, and the wrong-benign count as a number.
- **Ten of the 11 busy-deferred doorbells are `thinking [busy-attested]`** on the strength of the
  watcher's own record that the doorbell was deferred because the agent was busy. There was no activity
  sample to corroborate it in a replay. Live, the busy hook and the process tree are read as well.
- **Only two real stalls.** "Every real stall flagged" is 2 of 2; the confidence interval is wide.
- **Torn-down was judged from the lane map at replay time** in the first version. Now the adapter for that fleet resolves the lane as of the alarm (fleet ledger, the entry kept from before a relaunch) and answers `unknown` where it cannot, and a declared wait older than the machine's last boot or the lane's newest agent session no longer counts. The reason: on 2026-10-10, after a four-hour outage, 13 stuck lanes were called benign (a pause from before the restart, a lane read as torn-down that was live then). The burst's replay file is kept with that fleet's adapter, outside this repository.
- The hand labels for the 54 are the scout's counts per lane and class, assigned to individual alarms
  by reading each lane's own status line at the alarm's moment.
