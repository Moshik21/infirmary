# Repairs: tier 1, tier 2, plans and approval

The Infirmary repairs nothing by itself. While an operator has live repair turned on for the home (`infirmary live on`) it calls an **owner's own command** (a script or tool that already
owns the thing that broke), with the arguments that owner's dry run proposed, after re-checking every precondition right
before acting, and only for the small set of snags where the owner itself proves the target safe. Everything riskier is
written as an **inert plan** that a person approves. Advice-only (the default) only says what it would do.

```
$ infirmary live on --by me; infirmary check
...
repairs (mode live-repair):
[done] T1 reap-abandoned-workers · worker pid 160
          the check no longer finds it
[plan] T2 prune-merged-worktrees · box:home
          plan prune-merged-worktrees-1a2b3c4d waits for a person
plans waiting for a person:
  prune-merged-worktrees-1a2b3c4d · box:home · runs: pool-tool prune --all --yes
    infirmary approve prune-merged-worktrees-1a2b3c4d
```

**Turning live repair on is the fleet operator's decision, made explicitly with `infirmary live on`, per home; it is never
a default and nothing else turns it on.** It was proven on synthetic homes and shim owners only. Until the operator turns it
on, the Infirmary is advice-only, and `plan` and `approve` do anything a person wants done. `infirmary live off` (or the kill
switch, which wins over the toggle) stops it at once. What the toggle is, what it records and what still protects:
[safety.md](safety.md#live-repair-the-operators-switch).

## Modes

| Mode | What the Infirmary does with a finding a repair answers |
| --- | --- |
| `off` | nothing; it does not even look (also the kill switch) |
| `advise` (default; `advice-only` in status and `doctor.json`) | reports `advised`: "would run X after its dry run" or "would write a plan"; runs no dry run, writes no plan |
| `repair` (`live-repair`; only while `infirmary live on` is in force for this home) | runs tier-1 repairs through the gates below; writes tier-2 plans |

`mode` in the config, `--set mode=` and `INFIRMARY_MODE` take only `off` or `advise`; `repair` is refused there. `infirmary live on` is the only way into live-repair, and `check --no-write` always behaves as advise.

## Tiers

| Tier | Qualifies when | Who acts |
| --- | --- | --- |
| T0 | everything | the Infirmary reports (every finding) |
| **T1** | the owner's command is idempotent or reversible **and the owner itself proves its target safe**, and it touches no lane's worktree or branch, no session, no pool slot | the Infirmary, in `repair` mode |
| **T2** | anything else an owner can do: deleting data, moving a branch, restarting a session, re-arming a supervisor | a person: `infirmary approve PLAN` |
| T3 | force-pushes, ref deletion, discarding unlanded work, killing by name pattern, deleting what a dry run did not list | nobody; there is no code path for it |

The action set is **closed and lives in code** (an adapter's `actions` hook). Configuration can only hold an action back,
never widen one: `doctor.repair.actions.<name>` is `"plan"` (never run it unattended; plan it instead) or `"off"` (never
propose it). Any other value is a usage error. Configuration cannot add an action, change an argv or raise a tier.

## A tier-1 run, step by step

1. **Gates**, each with the number behind it in the refusal: the kill switch; the live-repair toggle (read again before reading 2 and before the act); the host circuit breaker (no T1 while
   `breaker_open_findings` (4) or more confirmed findings are open on the host, or while the host is critical: the host beat
   says `critical` or memory pressure is critical); the action's own breaker (`breaker_failures` (2) failed or unverified runs
   in a row hold it for `breaker_cooldown_s` (6 h) or until `infirmary reset ACTION`); flapping (a finding repaired
   `flap_repairs` (3) times within `flap_window_s` (24 h) is a bug, not a snag, and is not repaired again); the rate limits
   (`host_per_hour` 3, `action_per_hour` 2, `finding_per_hour` 1); the Infirmary-wide lock (one risky action at a time).
2. **Reading 1.** The owner's dry run (an argv from code, never a shell string), then the action's preconditions over what
   it listed: the output is understood, the target set is not empty (an empty set never widens scope) and not above the
   action's bound, every target is one the Infirmary itself proved, and so on per action.
3. **Reading 2, immediately before acting.** The kill switch again; the owner's dry run again; then the finding is detected
   again from scratch (a fresh process table) and the preconditions re-evaluated, so the identity check is the last thing
   read before the act. The targets must be exactly the targets of reading 1. Both readings are logged with their
   timestamps; the fixtures hold the gap from reading 2 to the act under one second.
4. **Act.** The owner's repair command, once, with a timeout (`timeout_s`).
5. **Verify.** The check runs again. The repair counts as `verified` only when the check no longer finds it; an exit 0 that
   leaves the finding is `not-cleared` and counts against the action's breaker, like a failure.

Outcomes: `advised`, `verified`, `not-cleared`, `failed`, `refused`, `cleared` (the finding went away before the act; nothing
ran), `planned`, `approved` (a handoff was recorded).

## Plans and `approve`

A plan is a JSON file in `<state>/plans/<id>.json` (`infirmary.plan 1.0`): the action, the findings, the owner's dry run
and its output (reading 1), the exact argv and working directory, the targets, why a person must decide, and an expiry
(`plan_ttl_s`, 1 h). It does nothing by existing. `infirmary plan [FINDING_ID ...]` writes plans for the current
findings in any mode (so a person can plan a tier-1 action while the Infirmary stays advice-only); live-repair writes them
for tier-2 actions, and for a tier-1 action whose own preconditions call for a person (for example a readiness `--fix`
that would also restart a session).

`infirmary approve PLAN_ID [--by NAME]` re-verifies everything and runs exactly that argv once:

- the kill switch, the plan's state (`proposed`; a plan runs at most once) and its expiry;
- the action is one this Infirmary knows (the closed set), and is not switched off;
- the findings are still found; the argv the Infirmary would build **now** equals the plan's (a hand-edited plan is never run);
- the owner's dry run lists exactly the plan's targets;
- then the command runs and the check verifies it, as in a tier-1 run. The log row names who approved.

Rate limits and breakers protect against automation storms; an approval is a person's deliberate act, so they do not apply
to it. The kill switch and the lock do. A plan whose world changed becomes `stale` and is never run; the next check writes a
fresh one if the finding is still there.

Some owners must carry a plan out themselves (a lane's worker moves its own branch; a supervisor re-arms its own watcher
as a tracked task). Those plans say `runs: owner`: `approve` re-verifies them and records the approval with the exact
handoff, and the Infirmary runs nothing.

## What is written, and what is shown

- `<state>/actions.jsonl`: one line per repair, refusal, plan, approval, handoff and breaker reset: time, action, tier,
  mode, findings, argv, dry-run argv, working directory, both readings (dry-run exit code and the end of its output, the
  targets, every precondition with its value), the exit code and the end of the owner's output, whether it verified, who
  approved, and the reason.
- `<state>/repairs.json`: the Infirmary's own memory of runs for the rate limits, breakers and flap detection (a week, at most
  500 runs).
- `<state>/plans/`: the plans. `<state>/repair.lock`: the lock.
- `doctor.json` gains a `repairs` block (`outcomes`, open `plans`, `breakers`). With `--push`, every run and refusal of the
  tick is a Lookout card (`repaired: ...` as `info`; `repair refused`, `repair failed`, `repair did not clear` as `action`)
  and every plan waiting for a person is a `decision` card whose `how` is the approve command.

Nothing else is written. The owners write what they own.

## Settings (`doctor.repair.*`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `actions.<name>` | (none) | `"plan"` or `"off"`: hold one action back |
| `host_per_hour` / `action_per_hour` / `finding_per_hour` | 3 / 2 / 1 | tier-1 runs allowed in the last hour (the report's "3 per host per hour, 1 per finding") |
| `breaker_open_findings` | 4 | an absolute floor, not a percentage: with three to five machines a percentage means nothing |
| `breaker_failures` / `breaker_cooldown_s` | 2 / 21600 | failed or unverified runs in a row that trip an action's breaker, and how long it stays tripped |
| `flap_repairs` / `flap_window_s` | 3 / 86400 | a finding repaired this often stops being repaired (systemd's `StartLimitBurst` idea) |
| `plan_ttl_s` | 3600 | a plan not approved within this is stale |
| `timeout_s` | 120 | the owner's repair command is ended after this |
| `output_lines` | 40 | lines of owner output kept per reading and per run |

## Adding an action (adapters only)

An action is a subclass of `infirmary.doctor.repair.Action` in an adapter, returned by its `hooks(cfg)["actions"]`:
`name`, `kinds` (the finding kinds it answers), `tier`, `runs`, `owner`, `max_targets`, `dry_rc`, and three methods:
`propose(ctx, findings)` builds the argv from code and from what the checks saw; `assess(ctx, proposal, dry, now)` reads
the owner's dry run against the findings as they are now and returns the targets and the preconditions; `verify` (optional)
says whether it worked. The finding it acts on records its `target` (a process identity, a path) so `assess` can prove the
thing is still the same thing. Every action comes with fixtures: the case it repairs and at least one refusing twin.
