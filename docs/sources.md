# Sources and adapters

A **source** turns "a lane" into an evidence dict. The contract:

```python
class Source:
    def __init__(self, cfg): ...
    def lanes(self):                                  # [{"id", "label"}]
    def resolve(self, lane=None, window=None):        # subject dict, or None
    def collect(self, subject, clock, trigger=None, as_of=None):   # evidence dict
    # optional
    def parse_alarm(self, text):                      # alarm text -> trigger dict
    def export_replay(self, out_path, cfg):           # write a replay file from recorded alarms
    def recorded_alarms(self, since=None, until=None):   # [{n, at, window, text, subject, trigger}]: what `infirmary advise` explains
    def outcome(self, lane, at, until):               # {events: [{kind, at, record}], instructed, unreadable: [why]}: what the lane did next (docs/judged.md)
    ADVICE_DIR = "infirmary"                       # class attribute: where an install keeps its files, relative to the home
```

`clock.left()` is the seconds left in the budget: skip a reader and say so in `sources` rather than overrun.
`as_of` set means "describe that past moment": use only records stamped at or before it, and skip
readers of present state (a file's mtime now says nothing about then). That includes the lane map: a lane torn down
or relaunched since is not as it stands today, so resolve it as of `as_of` or say `lane.known: false` with the reason.
Set `host.boot_at` and `lane.session_at` ([evidence](evidence.md)) if you can date them: a declared wait older than either does not count.

`outcome` is what the judged ledger ([judged.md](judged.md)) asks a source once an alarm's window has passed: the lane's own records stamped
inside `(at, until]`, as `events` of `kind` `relaunched` (a newer agent session, or the lane dispatched again), `gone` (cleaned up), `finished`
(a `done` line or a terminal outcome), `answered` (a `resolved` line) or `moved` (any other record of the lane's own), each with its `at` and the
name of the `record`; `instructed` when an instruction was delivered to the lane inside the window; and `unreadable`, the reasons a record that
should be there could not be read (an empty `events` with an empty `unreadable` means the lane's records were read and held nothing). Read-only,
from what `collect` reads. A source without it gets an outcome row that says so, never a guess.

An adapter is a directory `<name>/` holding `source.py` (and, for the doctor, `doctor.py`), found in a directory listed in `adapter_paths`; select one with
`--set adapter=<name>`. `adapter_config.*` is theirs to define (`--set adapter_config.home=...`).

`examples/adapter/files-home/` is a complete one to run or copy: `source.py` reads a `lanes.json` and an `alarms.jsonl` from a home, and `doctor.py`
adds one check, one incident rule and one repair action through an owner script ([its README](../examples/adapter/files-home/README.md)). A stranger's
install of it, over any folder that holds a `lanes.json`:

```
infirmary install --home ~/my-home --adapter files-home --advice-only \
    --set 'adapter_paths=["<checkout>/examples/adapter"]'
```

## The built-in `files` source

`--set files.lanes=PATH` points at a JSON file; relative paths resolve against it.

```json
{"lanes": {"lane-a": {"label": "Lane A", "window": "w1",
                      "declarations": "lane-a.log",
                      "agent": {"pid": 4242, "starttime": 12345, "cmdline": "python worker.py"},
                      "activity": ["out/*.log"],
                      "queue_classes": ["farm"], "tasks": ["lane-a"]}},
 "queues": {"farm": {"holders": "slots/farm.*", "tickets": "slots/farm.wait/*",
                     "ticket_re": "^(?P<tier>\\d)-(?P<arrival>\\d+)-(?P<pid>\\d+)$"}},
 "hosts": {"farm-b": {"beat": "beats/farm-b/beat.json", "canary_ok": true}}}
```

`agent` records the process identity (`infirmary.core.proc.identity(pid)` prints one). Holder files hold
`key=value` pairs ending with `cmd=...` (`pid`, `at`, `tier`, `task`, `host`); a holder counts when its
`task` is one of the lane's `tasks` and its pid is alive. A ticket counts only when a descendant of the
lane's agent holds it. A host beat is a `lookout.beat` file.

## Declaration log

One line per event: `state [key=slug] [at=EPOCH]: text`, `state` one of `working`, `paused`, `done`,
`blocked`, `needs-decision`, `failed`, `resolved`. `resolved [key=x]` closes the open decision `x`; with
no open decision left the worker owes work again. A `paused` line may state its length ("about 1-2h",
"until 2026-10-08T21:30Z"), which is how an overdue wait is told from a fresh one.

## Pushing verdicts to Lookout

`--push` (with `lookout.enabled`) sends the verdict of that invocation to a Lookout host as a source of
its own (`lookout.source_id`, default `stall-explain`): each lane is an item whose `state_detail` is the
verdict; only `wedged`, `dead`, `waiting-on-human` and `unknown` raise an attention card. Transports:
`http` (`lookout.target` is the base URL, token from `lookout.token_file` or `lookout.token_env`) or
`local` (`lookout.target` is the command, `lookout.args` the words before `ingest`, for example
`["--data", "/path"]`). A host that is down returns an error string and changes nothing else. One push
replaces the source's previous feed, so a wrapper that explains many lanes should collect them first.
