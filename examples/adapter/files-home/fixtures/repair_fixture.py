#!/usr/bin/env python3
"""Build the repair fixtures for the example adapter: a synthetic files-home with leftover job markers, a synthetic /proc and a shim owner script.

    python3 examples/adapter/files-home/fixtures/repair_fixture.py OUTDIR

Each scenario gets a world of its own (a repair changes the world: the owner shim removes the markers it lists), and `scripts/test` replays it
through `bin/infirmary` (check in advise and repair mode, plan, approve, reset, off, on). What is proven, through the adapter's one action
(`tidy-job-markers`, which runs the home's own `bin/jobs-tidy`) and so for the core's repair engine in general:

  - the kill switch (file, environment, thrown between the readings), the live-repair toggle (off until the operator's command, never turned on
    by configuration or a record from another home, turning it off stops a repair between its readings and returns to advice and plans),
  - the host circuit breaker (many open findings, a critical host), the action breaker tripping after runs that did not clear and `reset`, the
    rate limit, a finding that keeps coming back, configuration holding an action back to a plan (and never widening it),
  - plans: inert until `approve`, which re-verifies (changed candidates and a hand-edited plan are refused) and runs exactly the plan's argv once.

The shim is a small Python program. It writes its own marks under `<world>/shim-state`, never into the home.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import files_world  # noqa: E402
from files_world import Home  # noqa: E402
from fixlib import run, scenario  # noqa: E402
import fixlib  # noqa: E402

CAPTURE_SHIM = os.path.join(files_world.ROOT, "examples", "doctor", "shims", "lookout-capture")
PUSH = ["lookout.enabled=true", "lookout.transport=local", "lookout.target=" + CAPTURE_SHIM]
ACTION = "tidy-job-markers"


def fid(mid):
    return "leftover-job-marker:%s" % mid


class RepairWorld(Home):
    def __init__(self, root):
        Home.__init__(self, root)
        self.lane("lane-live", [("working", 600, "started the next step")])       # a lane lanes.json lists: a marker naming it is not a leftover
        self.save()

    def leftovers(self, ids, **cfg):
        """A marker for each id, naming a lane lanes.json does not list; the owner's shim lists exactly these (and removes them on its apply)."""
        listed = [(i, "lane-old-" + i) for i in ids]
        for mid, lane in listed:
            self.marker(mid, lane)
        self.owner(listed, **cfg)
        return listed

    def opts(self, extra=()):
        out = []
        for s in self.sets(extra):
            out += ["--set", s]
        return out

    def live(self, on=True, expect=None):
        """The operator turning the home's live-repair toggle on (or off), through the public command. Without it the Infirmary is
        advice-only, whatever else a run says; repair mode is not something configuration can ask for."""
        want = {"exit": 0, "text_has": ["live repair: " + ("ON" if on else "OFF")],
                "log": [{"kind": "live-repair", "to": "on" if on else "off", "by": "fixture"}]}
        want.update(expect or {})
        return run(["live", "on" if on else "off", "--by", "fixture"] + self.opts(), want, env=self.env(), text=True)

    def marker_file(self, mid):
        return os.path.join(self.home, "jobs", mid + ".job")


# ---- kill switch, toggle, breakers, rate limit, flapping ------------------------------------------------------------------------
def build_policy(out):
    scs = []
    w = RepairWorld(os.path.join(out, "switch"))
    w.leftovers(["m1"], throw_switch=True)
    scs.append(scenario("files-repair-kill-switch-file-env-and-mid-action", [w.live(),
        run(["off"] + w.opts(), {"exit": 0}, env=w.env(), text=True),
        run(["status"] + w.opts(), {"exit": 0, "text_has": ["live repair: ON", "the kill switch is on, so nothing acts"]}, env=w.env(), text=True),
        run(["check"] + w.opts(), {"exit": 0, "text_has": ["is off"], "files": {w.mark("dry-runs"): False}}, env=w.env(), text=True),
        run(["on"] + w.opts(), {"exit": 0}, env=w.env(), text=True),
        run(["check"] + w.opts(), {"exit": 0, "text_has": ["is off"], "files": {w.mark("dry-runs"): False}}, env=w.env(INFIRMARY="off"), text=True),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "refused"]], "reason_has": ["kill switch was thrown after the first reading"],
                                   "files": {w.mark("dry-runs"): True, w.mark("applies"): False, w.marker_file("m1"): True}}, env=w.env()),
        run(["check"] + w.opts(), {"exit": 0, "text_has": ["is off"]}, env=w.env(), text=True)]))

    # the toggle: advice-only until the operator's command, nothing else turns it on, and turning it off stops the Infirmary at once
    w = RepairWorld(os.path.join(out, "toggle"))
    w.leftovers(["m1"])
    cap = os.path.join(w.root, "feed.json")
    foreign = {"write_state": "live-repair.json", "json": {"schema": "infirmary.live-repair", "schema_version": "1.0", "enabled": True, "by": "someone", "at": "2026-01-01T00:00:00Z",
                                                          "scope": {"host": "another-host", "adapter": "files-home", "home": "/elsewhere"}}}
    garbage = {"write_state": "live-repair.json", "json": ["enabled", True]}
    advice = {"repairs": [[ACTION, "advised"]], "json_has": {"repair_mode": "advice-only", "mode": "advise"},
              "files": {w.mark("dry-runs"): False, w.mark("applies"): False}, "log_lacks": ["repair", "refusal"]}
    scs.append(scenario("files-repair-live-is-off-by-default-and-only-the-operators-command-turns-it-on", [
        run(["status", "--json"] + w.opts(), {"exit": 0, "json_has": {"repair_mode": "advice-only", "live_repair.enabled": False}}, env=w.env()),
        run(["live"] + w.opts(), {"exit": 0, "text_has": ["live repair: OFF", "never turned on", "advice-only"]}, env=w.env(), text=True),
        run(["check"] + w.opts(), dict(advice, exit=10), env=w.env()),
        run(["check", "--json"] + w.opts(), dict(advice, exit=10), env=w.env(INFIRMARY_MODE="advise")),
        run(["check"] + w.opts(["mode=repair"]), {"exit": 2, "text_has": []}, env=w.env(), text=True),
        run(["check"] + w.opts(), {"exit": 2}, env=w.env(INFIRMARY_MODE="repair"), text=True),
        run(["check"] + w.opts(["live_repair=true"]), {"exit": 2}, env=w.env(), text=True),
        run(["check"] + w.opts(), dict(advice, exit=10), env=w.env(INFIRMARY_LIVE="on", INFIRMARY_LIVE_REPAIR="1"), setup=[foreign]),
        run(["live"] + w.opts(), {"exit": 0, "text_has": ["live repair: OFF", "another host, adapter or home"]}, env=w.env(), text=True),
        run(["check"] + w.opts(), dict(advice, exit=10), env=w.env(), setup=[garbage]),
        run(["live"] + w.opts(), {"exit": 0, "text_has": ["live repair: OFF", "treated as off"]}, env=w.env(), text=True),
        run(["live", "maybe"] + w.opts(), {"exit": 2}, env=w.env(), text=True),
        w.live(),
        run(["live", "--json"] + w.opts(), {"exit": 0, "json_has": {"repair_mode": "live-repair", "live_repair.enabled": True, "live_repair.by": "fixture"}}, env=w.env()),
        run(["check"] + w.opts(PUSH) + ["--push"], {"repairs": [[ACTION, "verified"]], "json_has": {"repair_mode": "live-repair", "mode": "repair"},
                                                    "files": {w.mark("applies"): True, w.marker_file("m1"): False},
                                                    "feed": {"path": cap, "source": "infirmary", "label_has": "live-repair", "cards": ["repaired: job marker m1"]}},
            env=w.env(INFIRMARY_CAPTURE=cap)),
        run(["status", "--json"] + w.opts(), {"exit": 0, "json_has": {"repair_mode": "live-repair", "live_repair.enabled": True, "live_repair.by": "fixture", "mode": "repair"}}, env=w.env()),
        w.live(False),
        run(["status", "--json"] + w.opts(), {"exit": 0, "json_has": {"repair_mode": "advice-only", "live_repair.enabled": False, "live_repair.by": "fixture"}}, env=w.env())]))

    w = RepairWorld(os.path.join(out, "toggle-off"))
    w.leftovers(["m1"])
    scs.append(scenario("files-repair-turning-live-off-returns-to-advice-and-plans-and-approve-still-work", [w.live(), w.live(False),
        run(["check"] + w.opts(), {"exit": 10, "repairs": [[ACTION, "advised"]], "json_has": {"repair_mode": "advice-only"},
                                   "files": {w.mark("dry-runs"): False, w.mark("applies"): False}, "log_lacks": ["repair", "refusal"]}, env=w.env()),
        run(["plan", "--json"] + w.opts(), {"exit": 0, "plans": {ACTION: "proposed"}, "files": {w.mark("applies"): False}}, env=w.env()),
        run(["approve", "{plan:%s}" % ACTION, "--json", "--by", "fixture"] + w.opts(), {"exit": 0, "outcome": "verified", "plans": {ACTION: "done"},
                                                                                       "files": {w.mark("applies"): True, w.marker_file("m1"): False}}, env=w.env())]))

    w = RepairWorld(os.path.join(out, "toggle-mid-action"))
    w.leftovers(["m1"], throw_live_off=True)
    scs.append(scenario("files-repair-live-turned-off-between-the-readings-stops-the-repair", [w.live(),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "refused"]], "reason_has": ["live repair was turned off after the first reading"],
                                   "files": {w.mark("dry-runs"): True, w.mark("applies"): False, w.marker_file("m1"): True}}, env=w.env()),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "advised"]], "json_has": {"repair_mode": "advice-only"}, "files": {w.mark("applies"): False}}, env=w.env())]))

    w = RepairWorld(os.path.join(out, "many"))
    w.leftovers(["m1", "m2", "m3", "m4"])
    cap = os.path.join(w.root, "feed.json")
    scs.append(scenario("files-repair-circuit-breaker-many-open-findings", [w.live(),
        run(["check"] + w.opts(PUSH) + ["--push"], {"repairs": [[ACTION, "refused"]], "reason_has": ["4 findings are open on this host (limit 4)"],
                                                    "files": {w.mark("dry-runs"): False, w.mark("applies"): False},
                                                    "feed": {"path": cap, "source": "infirmary", "cards": ["repair refused: job marker m1"]}},
            env=w.env(INFIRMARY_CAPTURE=cap))]))

    w = RepairWorld(os.path.join(out, "critical"))
    w.leftovers(["m1"])
    w.proc.memory(total_gb=16, avail_pct=2, swap_gb=8, swap_used_pct=97, psi_full=30)
    scs.append(scenario("files-repair-circuit-breaker-critical-host", [w.live(),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "refused"]], "reason_has": ["the host is critical"],
                                   "files": {w.mark("dry-runs"): False, w.mark("applies"): False}}, env=w.env())]))

    w = RepairWorld(os.path.join(out, "trip"))
    w.leftovers(["m1"], clears=False)       # the owner says it removed the marker, but it is still there
    loose = ["doctor.repair.finding_per_hour=10", "doctor.repair.action_per_hour=10", "doctor.repair.host_per_hour=10"]
    scs.append(scenario("files-repair-rate-limit-then-breaker-trips-and-reset-closes-it", [w.live(),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "not-cleared"]], "reason_has": ["exited 0 but still found"]}, env=w.env()),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "refused"]], "reason_has": ["rate limit", "limit 1 per finding"]}, env=w.env()),
        run(["check"] + w.opts(loose), {"repairs": [[ACTION, "not-cleared"]], "breakers": {ACTION: True}}, env=w.env()),
        run(["check"] + w.opts(loose), {"repairs": [[ACTION, "refused"]], "reason_has": ["did not clear its finding 2 time(s) in a row"],
                                        "breakers": {ACTION: True}}, env=w.env()),
        run(["reset", ACTION, "--by", "fixture"] + w.opts(), {"exit": 0, "text_has": ["it was tripped"], "log": [{"kind": "reset"}]}, env=w.env(), text=True),
        run(["check"] + w.opts(loose), {"repairs": [[ACTION, "not-cleared"]], "breakers": {ACTION: False}}, env=w.env())]))

    w = RepairWorld(os.path.join(out, "flap"))
    w.leftovers(["m1"])
    seed = {"seed_runs": {"action": ACTION, "finding": fid("m1"), "outcome": "verified", "ago_s": [80000, 40000, 7200]}}
    scs.append(scenario("files-repair-a-finding-that-keeps-coming-back-is-not-repaired-again", [w.live(),
        run(["check"] + w.opts(), {"repairs": [[ACTION, "refused"]], "reason_has": ["repair keeps recurring", "this is a bug"],
                                   "files": {w.mark("dry-runs"): False}}, env=w.env(), setup=[seed])]))

    w = RepairWorld(os.path.join(out, "held"))
    w.leftovers(["m1"])
    scs.append(scenario("files-repair-config-can-hold-an-action-back-but-not-widen-it", [w.live(),
        run(["check"] + w.opts(["doctor.repair.actions.%s=plan" % ACTION]), {"repairs": [[ACTION, "planned"]],
                                                                            "plans": {ACTION: "proposed"}, "files": {w.mark("applies"): False}}, env=w.env()),
        run(["check"] + w.opts(["doctor.repair.actions.%s=off" % ACTION]), {"repairs": []}, env=w.env()),
        run(["check"] + w.opts(["doctor.repair.actions.%s=run" % ACTION]), {"exit": 2, "text_has": []}, env=w.env(), text=True)]))
    return scs


# ---- plans and approve ------------------------------------------------------------------------------------------------------------
def build_approve(out):
    scs = []
    hold = ["doctor.repair.actions.%s=plan" % ACTION]       # configuration may hold an action back: it is then only ever a plan

    w = RepairWorld(os.path.join(out, "changed-twin"))
    w.leftovers(["m1", "m2"])
    w.owner([("m1", "lane-old-m1")], variants={"changed": [("m1", "lane-old-m1"), ("m2", "lane-old-m2")]})
    env = w.env()
    scs.append(scenario("files-repair-approve-refuses-when-the-candidates-changed", [w.live(),
        run(["check"] + w.opts(hold), {"repairs": [[ACTION, "planned"]], "plans": {ACTION: "proposed"}}, env=env),
        run(["approve", "{plan:%s}" % ACTION, "--json"] + w.opts(hold), {"exit": 1, "outcome": "refused", "reason_has": ["the dry run now lists"],
                                                                         "plans": {ACTION: "stale"}, "files": {w.mark("applies"): False}}, env=w.env(TIDY_VARIANT="changed")),
        run(["approve", "{plan:%s}" % ACTION, "--json"] + w.opts(hold), {"exit": 1, "outcome": "refused", "reason_has": ["this plan is stale"],
                                                                         "files": {w.mark("applies"): False}}, env=env)]))

    w = RepairWorld(os.path.join(out, "tamper-twin"))
    w.leftovers(["m1"])
    scs.append(scenario("files-repair-approve-never-runs-a-hand-edited-plan", [w.live(),
        run(["check"] + w.opts(hold), {"repairs": [[ACTION, "planned"]]}, env=w.env()),
        run(["approve", "{plan:%s}" % ACTION, "--json"] + w.opts(hold), {"exit": 1, "outcome": "refused", "reason_has": ["never run as written"],
                                                                         "files": {w.mark("applies"): False, w.marker_file("m1"): True}}, env=w.env(),
            setup=[{"edit_plan": ACTION, "set": {"apply": [os.path.join(w.home, "bin", "jobs-tidy"), "--and-everything-else"]}}])]))
    return scs


def main():
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    scs = []
    for fn in (build_policy, build_approve):
        scs += fn(out)
    fixlib.save(out, scs)
    print("wrote %d scenarios under %s" % (len(scs), out))


if __name__ == "__main__":
    main()
