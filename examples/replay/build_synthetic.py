#!/usr/bin/env python3
"""Write src/infirmary/explain/synthetic.jsonl: one invented alarm per verdict and per interesting edge.

    python3 examples/replay/build_synthetic.py > src/infirmary/explain/synthetic.jsonl

Nothing here is real: the lanes, hosts and commands are made up so the demo exercises every verdict
and the accuracy gate without any setup. Recorded alarms from a real fleet live with their adapter.
"""
import json

T0 = 1_800_000_000
CLASSES = {
    "real-stall": {"expect": "flagged"},
    "supervisor-owed": {"expect": ["waiting-on-supervisor"], "benign": True},
    "declared-wait": {"expect": ["declared-wait", "running-long", "running-remote", "queued"], "benign": True},
    "live-run": {"expect": ["running-long", "running-remote", "queued", "thinking"], "benign": True},
    "busy-deferred": {"expect": ["thinking", "running-long", "running-remote"], "benign": True},
    "torn-down": {"expect": ["torn-down"], "benign": True},
}


def ev(name, at, **kw):
    base = {"schema": "infirmary.evidence", "schema_version": "1.0", "subject": {"id": name, "label": name}, "as_of": at,
            "lane": {"known": True, "closed": False}, "trigger": {}, "declarations": [], "agent": {}, "activity": {},
            "jobs": [], "history": {}, "hosts": {}, "pipeline": {}, "sources": []}
    base.update(kw)
    return base


def decl(at, state, text, key=None, **kw):
    row = {"at": at, "state": state, "key": key, "text": text, "wait_hint_s": None, "until": None}
    row.update(kw)
    return row


def main():
    alarms = []

    def add(aid, cls, evidence, note):
        alarms.append({"schema": "infirmary.replay", "schema_version": "1.0", "kind": "alarm", "id": aid, "at": evidence["as_of"],
                       "class": cls, "note": note, "evidence": evidence})

    a = T0 + 3600
    add("s01", "supervisor-owed", ev("lane-docs", a, declarations=[decl(a - 2040, "working", "writing"), decl(a - 2040 + 60, "done", "docs committed; ready for review")],
        trigger={"kind": "silence"}), "finished lane waiting to be collected")
    add("s02", "supervisor-owed", ev("lane-api", a, declarations=[decl(a - 900, "needs-decision", "which schema?", key="schema")],
        trigger={"kind": "silence"}), "open question for the supervisor")
    add("s03", "declared-wait", ev("lane-bench", a, declarations=[decl(a - 1800, "paused", "benchmark farm running, about 1-2h", wait_hint_s=7200)],
        trigger={"kind": "silence"}), "declared two-hour wait, 30 minutes in")
    add("s04", "live-run", ev("lane-build", a, jobs=[{"kind": "holder", "class": "build", "host": None, "tier": "2", "started": a - 1500, "held_s": 1500,
        "cmd": "cargo build --release", "cmd_key": "cargo build", "alive": True, "source": "slot holder file"}],
        history={"cargo build": {"n": 31, "p50_s": 1700, "p90_s": 2400}}, trigger={"kind": "wedge-timer", "idle_s": 260, "escalation": 1}),
        "long build inside its usual length")
    add("s05", "live-run", ev("lane-floor", a, jobs=[{"kind": "holder", "class": "floor", "host": "farm-b", "tier": "2", "started": a - 900, "held_s": 900,
        "cmd": "run-suite --all", "cmd_key": "run-suite --all", "alive": True, "source": "slot holder file"}],
        hosts={"farm-b": {"beat_age_s": 12, "level": "ok", "canary_ok": True, "canary_age_s": 200}}, trigger={"kind": "wedge-timer", "idle_s": 250, "escalation": 2}),
        "job on a healthy remote host")
    add("s06", "live-run", ev("lane-floor2", a, jobs=[{"kind": "holder", "class": "floor", "host": "farm-b", "tier": "2", "started": a - 900, "held_s": 900,
        "cmd": "run-suite --all", "cmd_key": "run-suite --all", "alive": True, "source": "slot holder file"}],
        hosts={"farm-b": {"beat_age_s": 20, "level": "warn", "canary_ok": True, "canary_age_s": 100}}, trigger={"kind": "wedge-timer", "idle_s": 250, "escalation": 1}),
        "same, but the host is under pressure: slow-host qualifier")
    add("s07", "declared-wait", ev("lane-queue", a, jobs=[{"kind": "ticket", "class": "farm", "tier": "2", "ahead": 2, "held_s": 600, "alive": True, "source": "slot ticket"}],
        declarations=[decl(a - 600, "paused", "queued behind two farm jobs")], trigger={"kind": "silence"}), "waiting for a slot")
    add("s08", "busy-deferred", ev("lane-think", a, agent={"state": "busy", "source": "doorbell-deferral"},
        trigger={"kind": "doorbell-busy", "attempts": 2}), "instruction deferred while the agent is mid-turn")
    add("s09", "real-stall", ev("lane-prompt", a, trigger={"kind": "agent-blocked"}, agent={"state": "blocked", "source": "agent-state"}),
        "a prompt only a person can clear")
    add("s10", "real-stall", ev("lane-stuck", a, trigger={"kind": "silence", "idle_s": 2400},
        declarations=[decl(a - 5000, "working", "started")], activity={"newest_age_s": 4000, "grew": False}, agent={"alive": True, "cpu_cores": 0.0, "state": "idle"}),
        "idle 40 minutes with nothing behind it: wedged")
    add("s11", "real-stall", ev("lane-gone", a, agent={"alive": False, "source": "process identity"}, trigger={"kind": "silence"}), "agent process is gone: dead")
    add("s12", "torn-down", ev("lane-old", a, lane={"known": True, "closed": True, "note": "no live record for this lane"}, trigger={"kind": "silence"}), "lane already removed")
    add("s13", "real-stall", ev("lane-late", a, declarations=[decl(a - 20000, "paused", "farm run, about 1h", wait_hint_s=3600)], trigger={"kind": "silence"}),
        "declared one hour, now over five hours: overdue, flagged")
    add("s14", "real-stall", ev("lane-doorbell", a, trigger={"kind": "doorbell-idle", "attempts": 3}), "instruction unread after three attempts, pane idle")
    add("s15", "live-run", ev("lane-fix", a, pipeline={"state": "round-active", "step": "test", "round": 2, "trigger": "auto_fix", "since": a - 700, "source": "pipeline ledger"},
        trigger={"kind": "wedge-timer", "idle_s": 280}), "a pipeline fix round is running")
    print(json.dumps({"schema": "infirmary.replay", "schema_version": "1.0", "kind": "header", "title": "synthetic demo set (invented lanes)",
                      "classes": CLASSES}, sort_keys=True))
    for row in alarms:
        print(json.dumps(row, sort_keys=True))


if __name__ == "__main__":
    main()
