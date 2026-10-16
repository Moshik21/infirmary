# files-home: an example adapter over plain files

A small, complete adapter for a "home" that is a folder of files. Copy it and replace the file names with the records of your own tool;
it is the shape the Infirmary's scheduled advice, incident rules and repairs need, with nothing in it that belongs to one tool.

## Use it as it is

```
python3 examples/demo-fleet/make_demo.py DEMO          # a folder with a lanes.json (any folder that holds one will do)
bin/infirmary install --home DEMO --adapter files-home --advice-only --dry-run \
    --set 'adapter_paths=["/path/to/this/checkout/examples/adapter"]'
```

`adapter_paths` lists the directories that hold adapters; the one named by `--adapter` is `<entry>/<name>/` (here
`examples/adapter/files-home/`). Use an absolute path: a cron line runs from another working directory. Drop `--dry-run` to schedule the
advise pass (one crontab line, or one LaunchAgent on macOS); `infirmary uninstall --home DEMO` takes it out again. The install writes its
files, and the `adapter_paths` entry, into `DEMO/infirmary/infirmary.json`, so the scheduled run finds the adapter again.

## What it reads

| File under the home | What it is | Needed |
|---|---|---|
| `lanes.json` | the lanes, in the shape of the built-in `files` source ([docs/sources.md](../../../docs/sources.md)) | yes: a home without it is refused |
| `<lane>.log` | a lane's declaration log, named by its `lanes.json` entry (`working`, `paused`, `done`, `failed` ... lines) | per lane |
| `alarms.jsonl` | the alarms a watcher recorded, one JSON object per line: `{"at": 1790000000, "lane": "lane-a", "text": "idle 3600s, possible wedge"}` | for `advise` to explain anything |
| `jobs/<id>.job` | a marker a job leaves while it runs: a `lane=NAME` line | for the example check |
| `bin/jobs-tidy` | the owner's script that removes markers (see below) | for the example action |

Setting: `adapter_config.home`, the folder above (`install --home` sets it).

An alarm whose text says `idle <N>s` is read as that lane having been silent for N seconds; an alarm for a lane `lanes.json` does not list is
read as a lane that was taken down (nothing to wake). Everything else about an alarm is explained from the lane's own declaration log, as of the
alarm's moment, by the same engine as any other source ([docs/verdicts.md](../../../docs/verdicts.md)).

## What is in it

| File | What it does |
|---|---|
| `source.py` | `Source`: the built-in `files` source over `<home>/lanes.json`, plus `recorded_alarms` (what `infirmary advise` explains), `outcome` (what each lane did next, for the judged ledger, [docs/judged.md](../../../docs/judged.md)) and `ADVICE_DIR = "infirmary"` (where an install keeps its files). Read-only |
| `doctor.py` | `hooks(cfg)`: **one check** (`job-markers`, catalogue 90: a marker names a lane `lanes.json` does not have), **one incident rule** (`lane-failed`, catalogue 91: a lane's own log says `failed`; one card and one row in `incidents.jsonl`, [docs/incidents.md](../../../docs/incidents.md)) and **one action** (`tidy-job-markers`, [docs/repairs.md](../../../docs/repairs.md)) |
| `fixtures/` | the scenarios `scripts/test` replays through the public commands (below) |

**The action goes through an owner.** The Infirmary never removes a marker itself. `tidy-job-markers` runs the home's own `bin/jobs-tidy`: first
`jobs-tidy --dry-run`, which must print only lines of the form `would remove job marker ID (lane NAME has no record)`, then `jobs-tidy`, which removes
them. The action refuses unless the dry run lists at least one marker, at most 16, only markers the check found, each still the file the check
read, each for the lane the check saw, and none for a lane that has a record again; and it counts the repair as done only when the check no
longer finds the markers. It is tier 1 (run unattended, only while the operator's `infirmary live on` is set for the home); a person can hold it
back to a plan with `--set doctor.repair.actions.tidy-job-markers=plan`.

## Make it yours

1. Copy the folder (`cp -r examples/adapter/files-home ~/my-adapters/my-tool`) and keep it in `adapter_paths`.
2. In `source.py`, read your tool's records instead of `lanes.json` and `alarms.jsonl`: `lanes`, `resolve`, `collect` and `recorded_alarms` are the
   contract ([docs/sources.md](../../../docs/sources.md)); set `ADVICE_DIR`.
3. In `doctor.py`, replace the three examples with checks, rules and owner commands of your tool. A finding says what it saw and never acts; an
   action only calls the owner's own dry run and repair command and verifies by reading again.
4. Keep the fixtures: each scenario is a synthetic home, a synthetic `/proc` and a shim owner, replayed through `bin/infirmary`, with a healthy
   look-alike that differs from the failure by one fact.

## The fixtures

`fixtures/advice_fixture.py` and `fixtures/repair_fixture.py` build the scenarios; `scripts/test` finds every
`examples/adapter/*/fixtures/*_fixture.py` and replays them (`files_world.py` is their shared synthetic home). They prove the core's machinery
through this adapter alone:

- **advice and install** (13): the window explained and its card; the judged ledger over a rotated alarm log; the install on cron and on launchd
  (one line or one agent, a second install does not double it, the advice run never repairs even with live repair on, uninstall restores the
  crontab byte for byte); the refusals; the run's own budget (backing off under load and coming back, the battery cases, the wall and CPU limits);
- **repairs** (11): the kill switch (file, environment, between the readings); live repair off by default and turned on only by the operator;
  the circuit breakers (many open findings, a critical host); the rate limit, the action breaker and `reset`; a finding that keeps coming back;
  configuration holding an action back and never widening it; `plan` and `approve` (a changed candidate list and a hand-edited plan are refused).

No real scheduler, process or home is touched: `crontab` and `launchctl` are shims, and the owner is a script that writes its marks under the
scenario's own directory.
