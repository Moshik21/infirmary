#!/usr/bin/env python3
"""Build the fixtures for the advice run and the per-home install, against the example adapter: a synthetic files-home and scheduler shims.

    python3 examples/adapter/files-home/fixtures/advice_fixture.py OUTDIR

The home holds six lanes' declaration logs, a lanes.json and an alarms.jsonl: an alarm for a lane that finished (the supervisor's move), a
lane in a declared wait, a lane that lanes.json no longer lists (taken down), a lane quiet for four minutes (unknown), a lane wedged for hours
(real), a lane quiet for five minutes (unknown), and one alarm older than the window. `crontab` and `launchctl` are shims that keep their
state in files under OUTDIR, so `scripts/test` can prove that install adds exactly one line (or one LaunchAgent), that a second install does
not double it, that the advice run never repairs even with live repair on, and that uninstall leaves the crontab byte for byte as it was.
No real scheduler is touched. The adapter is found the way a stranger's install finds it: through `adapter_paths`.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import files_world  # noqa: E402
from files_world import NOW, Home  # noqa: E402
from fixlib import run, scenario, write  # noqa: E402
import fixlib  # noqa: E402

MARK = "# infirmary advice "
ADAPTER = "files-home"

CRONTAB = r'''#!/bin/sh
# crontab shim: the user's crontab is the file $FIXTURE_CRONTAB
case "$1" in
  -l) [ -f "$FIXTURE_CRONTAB" ] || { echo "no crontab for fixture" >&2; exit 1; }; cat "$FIXTURE_CRONTAB" ;;
  -) cat > "$FIXTURE_CRONTAB" ;;
  *) echo "crontab shim: unexpected $*" >&2; exit 2 ;;
esac
'''

LAUNCHCTL = r'''#!/bin/sh
# launchctl shim: loaded labels are lines of $FIXTURE_LAUNCHD; every call is noted in $FIXTURE_LAUNCHD.calls
echo "$*" >> "$FIXTURE_LAUNCHD.calls"
touch "$FIXTURE_LAUNCHD"
case "$1" in
  bootstrap) label=$(basename "$3" .plist); grep -qx "$label" "$FIXTURE_LAUNCHD" || echo "$label" >> "$FIXTURE_LAUNCHD" ;;
  bootout) label=${2##*/}; grep -vx "$label" "$FIXTURE_LAUNCHD" > "$FIXTURE_LAUNCHD.new"; mv "$FIXTURE_LAUNCHD.new" "$FIXTURE_LAUNCHD" ;;
  print) label=${2##*/}; grep -qx "$label" "$FIXTURE_LAUNCHD" || { echo "Could not find service" >&2; exit 113; } ;;
  *) echo "launchctl shim: unexpected $*" >&2; exit 2 ;;
esac
'''


def world(root, failed=False):
    """A home with the six alarms in the window (3 explained, 1 real, 2 unknown) and one older. `failed` adds a lane that declared itself failed."""
    h = Home(root)
    h.lane("lane-done", [("working", 7200, "started"), ("done", 3600, "ready for review")])
    h.lane("lane-wait", [("working", 7200, "started"), ("paused", 1200, "waiting on the review gate, about 2h")])
    h.lane("lane-stuck", [("working", 10800, "started")])
    h.lane("lane-quiet", [("working", 600, "started the next step")])
    h.lane("lane-hush", [("working", 600, "started the next step")])
    if failed:
        h.lane("lane-failed", [("working", 7200, "started"), ("failed", 1800, "the build broke")])
    h.alarm("lane-done", 30 * 3600, "idle 600s, possible wedge")             # outside the 24 h window
    h.alarm("lane-done", 1800, "idle 300s, possible wedge")                  # finished: the supervisor's move
    h.alarm("lane-wait", 600, "idle 300s, possible wedge")                   # a declared wait
    h.alarm("lane-gone", 300, "idle 3600s, possible wedge")                  # lanes.json has no entry for it: taken down, nobody to wake
    h.alarm("lane-hush", 900, "idle 240s, possible wedge")                   # four minutes quiet: below the wedge floor, so undecided
    h.alarm("lane-stuck", 120, "idle 10800s, possible wedge")                # hours quiet, nothing declared that explains it: real
    h.alarm("lane-quiet", 60, "idle 300s, possible wedge")                   # five minutes quiet: below the wedge floor, so undecided
    h.save()
    return h


def shims(root):
    d = os.path.join(root, "shims")
    write(os.path.join(d, "crontab"), CRONTAB, 0o755)
    write(os.path.join(d, "launchctl"), LAUNCHCTL, 0o755)
    return d


def cfg_for(h, adv):
    return ["--set", "state_dir=" + adv]


def build_window(root):
    """advise over the window: the counts, the kinds, the open lanes, the card; the home's records untouched."""
    h = world(root, failed=True)
    adv = os.path.join(root, "advice-state")
    os.makedirs(os.path.join(root, "board"), exist_ok=True)       # a board's drop directory
    cfg = cfg_for(h, adv)
    unchanged = [{"path": h.home}]
    return [scenario("files-advice-explains-the-window", [
        run(h.args("advise", "--json") + cfg, {"exit": 0, "unchanged_but": unchanged, "json_has": {
            "mode": "advice-only", "alarms.available": True, "alarms.window_s": 86400, "alarms.summary.total": 6, "alarms.summary.explained": 3,
            "alarms.summary.real": 1, "alarms.summary.unknown": 2, "alarms.summary.lanes": 6, "alarms.summary.open_lanes": ["lane-hush", "lane-quiet", "lane-stuck"],
            "alarms.summary.by_explanation": {"declared-wait": 1, "torn-down": 1, "waiting-on-supervisor": 1}, "card_tool": "stall-explain"},
            "json_file": {os.path.join(adv, "card.json"): {"tool": "stall-explain", "status": "warn", "mode": "advice-only"}},
            "card_valid": [os.path.join(adv, "card.json")],
            "file_has": {os.path.join(adv, "advice.txt"): ["advice-only: nothing is repaired", "alarms in the last 24h: 6 · explained 3 · real 1 · unknown 2",
                                                           "explained by: declared-wait 1, torn-down 1, waiting-on-supervisor 1", "  - lane-stuck: wedged at ",
                                                           "  - lane-quiet: unknown at ",
                                                           # the example adapter's incident rule: the lane that declared itself failed is one open incident
                                                           "incidents: 1 open · 0 unknown · 1 of 1 rule(s) read", "what: lane lane-failed declared it failed: the build broke"],
                         os.path.join(adv, "card.json"): ["1 incident open (lane-failed)", '"label": "incidents open"'],
                         os.path.join(adv, "incidents.jsonl"): ["lane-failed"],
                         os.path.join(adv, "actions.jsonl"): ['"kind":"advise"']},
            "file_lacks": {os.path.join(adv, "actions.jsonl"): ['"kind":"repair"']}}),
        run(h.args("advise", "--json", "--set", "advice.window_s=200") + cfg, {"exit": 0, "json_has": {
            "alarms.summary.total": 2, "alarms.summary.real": 1, "alarms.summary.unknown": 1, "alarms.summary.explained": 0}}),
        run(h.args("advise", "--json", "--set", "advice.label=box-a", "--set", "advice.card=" + os.path.join(root, "board")) + cfg, {"exit": 0,
            "json_file": {os.path.join(root, "board", "stall-explain-box-a.json"): {"tool": "stall-explain-box-a", "title": "Alarms explained · box-a"}},
            "card_valid": [os.path.join(root, "board", "stall-explain-box-a.json")]}),
        run(h.args("advise", "--no-write", "--json") + ["--set", "state_dir=" + os.path.join(root, "never")],
            {"exit": 0, "json_has": {"alarms.summary.total": 6}, "files": {os.path.join(root, "never"): False}}),
        run(["off"] + cfg, {"exit": 0}),
        run(h.args("advise") + cfg, {"exit": 0, "text": True, "json_file": {os.path.join(adv, "card.json"): {"status": "unknown", "headline": "off (kill switch); no alarms explained"}},
                                     "file_has": {os.path.join(adv, "advice.txt"): ["the kill switch is on"]}}, text=True),
    ])]


def build_rotated(root):
    """The alarm log trimmed from the head between two advise runs: every alarm still in the window gets a new number, and the
    judged ledger still holds one verdict row for each."""
    h = world(root)
    adv = os.path.join(root, "advice-state")
    led = os.path.join(adv, "judged.jsonl")
    cfg = cfg_for(h, adv)
    return [scenario("files-advice-judged-survives-a-rotated-log", [
        run(h.args("advise", "--json") + cfg, {"exit": 0, "json_has": {"alarms.summary.total": 6, "alarms.judged.verdicts_appended": 6},
                                               "file_count": {led: {'"row":"verdict"': 6}}}),
        run(h.args("advise", "--json") + cfg, {"exit": 0, "json_has": {"alarms.summary.total": 6, "alarms.judged.verdicts_appended": 0},
                                               "file_count": {led: {'"row":"verdict"': 6}}},
            setup=[{"drop_head": os.path.join(h.home, "alarms.jsonl"), "lines": 1}]),
    ])]


def machine(h, load5=0.2, cores=4, power=None):
    """The machine the run reads about itself: a synthetic loadavg and cpuinfo in the fixture's /proc, and (or not) a power supply directory."""
    write(os.path.join(h.proc.root, "loadavg"), "%.2f %.2f %.2f 1/100 1\n" % (load5, load5, load5))
    write(os.path.join(h.proc.root, "cpuinfo"), "".join("processor\t: %d\nmodel name\t: fixture\n\n" % n for n in range(cores)))
    supply = os.path.join(h.root, "power")
    for name, rows in (power or {}).items():
        for fname, text in rows.items():
            write(os.path.join(supply, name, fname), text + "\n")
    return supply


def load_text(load5):
    return "%.2f %.2f %.2f 1/100 1\n" % (load5, load5, load5)


def build_budget(root):
    """The run's own cost: it steps aside, saying so, when the machine is loaded or on battery, and is back when that clears; it keeps a wall and a
    CPU budget and says what it left undone. Every look-alike (idle machine, on mains, a charged battery, a limit not reached) runs in full."""
    h = world(root)
    adv = os.path.join(root, "advice-state")
    cfg = cfg_for(h, adv)
    unchanged = [{"path": h.home}]
    loadavg = os.path.join(h.proc.root, "loadavg")
    machine(h, load5=3.6, cores=4)                                  # 0.90 per core: over the 0.80 limit
    gone = {"files": {os.path.join(adv, "doctor.json"): False, os.path.join(adv, "judged.jsonl"): False}}
    skipped = {"exit": 0, "unchanged_but": unchanged, "json_has": {"budget.decision": "skipped", "budget.reason": "load 0.90 per core (limit 0.80)", "doctor": None, "alarms": None},
               "json_file": {os.path.join(adv, "card.json"): {"status": "unknown", "mode": "advice-only",
                                                              "headline": "skipped on purpose: load 0.90 per core (limit 0.80); nothing was checked or explained this run"}},
               "card_valid": [os.path.join(adv, "card.json")],
               "file_has": {os.path.join(adv, "advice.txt"): ["skipped on purpose: load 0.90 per core (limit 0.80); nothing was checked or explained this run, and that is not a pass"],
                            os.path.join(adv, "actions.jsonl"): ['"budget":"skipped"', '"budget_reason":"load 0.90 per core (limit 0.80)"']}}
    skipped.update(gone)
    ok_run = {"exit": 0, "json_has": {"budget.decision": "full", "budget.reason": None, "budget.backed_off_runs": 2, "alarms.summary.total": 6, "alarms.summary.explained": 3},
              "file_has": {os.path.join(adv, "advice.txt"): ["back after 2 skipped run(s) since ", "alarms in the last 24h: 6 · explained 3"]},
              "json_file": {os.path.join(adv, "card.json"): {"status": "warn"}}, "files": {os.path.join(adv, "doctor.json"): True}}
    scs = [scenario("files-advice-backs-off-under-load-and-comes-back", [
        run(h.args("advise", "--json") + cfg, skipped),
        run(h.args("advise", "--json") + cfg, dict(skipped, json_has=dict(skipped["json_has"], **{"budget.backed_off_runs": 1}))),
        run(h.args("advise", "--json") + cfg, ok_run, setup=[{"write_file": loadavg, "text": load_text(0.8)}]),       # 0.20 per core: the machine is calm again
        run(h.args("advise", "--json") + cfg, {"exit": 0, "json_has": {"budget.decision": "full", "budget.backed_off_runs": 0, "alarms.summary.total": 6}, "file_lacks": {
            os.path.join(adv, "advice.txt"): ["back after", "skipped on purpose"]}}),
        run(h.args("advise", "--json", "--set", "advice.budget.load_per_core_max=0.1") + cfg, {"exit": 0, "json_has": {"budget.decision": "skipped", "budget.reason": "load 0.20 per core (limit 0.10)"}}),
        run(h.args("advise", "--json", "--set", "advice.budget.enabled=false", "--set", "advice.budget.load_per_core_max=0.1") + cfg,
            {"exit": 0, "json_has": {"budget.decision": "full", "alarms.summary.total": 6}}),
    ])]
    # a load that cannot be read is shown as not read, never as zero and never as a pass: the run goes ahead and says so
    h2 = world(os.path.join(root, "unread"))
    adv2 = os.path.join(root, "unread", "advice-state")
    cfg2 = cfg_for(h2, adv2)
    scs.append(scenario("files-advice-says-when-it-could-not-read-the-load", [
        run(h2.args("advise", "--json") + cfg2, {"exit": 0, "json_has": {"budget.decision": "full", "budget.load.per_core": None, "budget.power.on_battery": None, "alarms.summary.total": 6},
                                                 "file_has": {os.path.join(adv2, "advice.txt"): ["budget: load not read: ", "budget: power not read: "]}}),
    ]))
    # on battery: below the floor it steps aside, above it the wall budget is cut; on mains, or with no battery at all, it runs in full
    for name, supply, want, extra in (
            ("files-advice-skips-on-a-low-battery", {"BAT0": {"type": "Battery", "status": "Discharging", "capacity": "12"}, "AC": {"type": "Mains", "online": "0"}},
             {"budget.decision": "skipped", "budget.reason": "on battery at 12% (floor 30%)", "alarms": None}, ["skipped on purpose: on battery at 12% (floor 30%)"]),
            ("files-advice-shortens-on-a-charged-battery", {"BAT0": {"type": "Battery", "status": "Discharging", "capacity": "80"}, "AC": {"type": "Mains", "online": "0"}},
             {"budget.decision": "shortened", "budget.wall_s": 10.0, "alarms.summary.total": 6}, ["shortened on purpose: on battery at 80%: wall budget cut to 10 s"]),
            ("files-advice-runs-in-full-on-mains-power", {"BAT0": {"type": "Battery", "status": "Charging", "capacity": "12"}, "AC": {"type": "Mains", "online": "1"}},
             {"budget.decision": "full", "budget.power.on_battery": False, "alarms.summary.total": 6}, None),
            ("files-advice-runs-in-full-with-no-battery", {"AC": {"type": "Mains", "online": "1"}},
             {"budget.decision": "full", "budget.power.on_battery": False, "alarms.summary.total": 6}, None)):
        hb = world(os.path.join(root, name))
        advb = os.path.join(root, name, "advice-state")
        power = machine(hb, load5=0.4, cores=4, power=supply)
        scs.append(scenario(name, [run(hb.args("advise", "--json", "--set", "advice.budget.power_root=" + power) + cfg_for(hb, advb),
                                       {"exit": 0, "json_has": want, "file_has": {os.path.join(advb, "advice.txt"): extra or ["advice-only: nothing is repaired"]},
                                        "file_lacks": {os.path.join(advb, "advice.txt"): [] if extra else ["skipped on purpose", "shortened on purpose"]}})]))
    # the wall budget: the explanations stop when it is used up, and the run says how many alarms it left (never explained, never dropped)
    hw = world(os.path.join(root, "wall"))
    advw = os.path.join(root, "wall", "advice-state")
    cfgw = cfg_for(hw, advw)
    scs.append(scenario("files-advice-keeps-its-wall-budget-and-says-what-it-left", [
        run(hw.args("advise", "--json", "--set", "advice.budget.wall_s=0.001") + cfgw, {
            "exit": 0, "json_has": {"budget.decision": "full", "budget.cut": True, "alarms.summary.total": 0, "alarms.summary.left_unexplained": 6},
            "json_file": {os.path.join(advw, "card.json"): {"status": "unknown"}}, "card_valid": [os.path.join(advw, "card.json")],
            "file_has": {os.path.join(advw, "advice.txt"): ["cut short on purpose: the wall budget of 0.001 s was used, so 6 alarm(s) and 0 lane(s) asked again now were not explained (not a pass; the next run takes them)"],
                         os.path.join(advw, "card.json"): ["cut short on purpose (wall budget 0.001 s): 6 alarm(s) and 0 lane(s) not explained", '"label": "lanes not explained"']}}),
        run(hw.args("advise", "--json") + cfgw, {"exit": 0, "json_has": {"budget.decision": "full", "budget.cut": False, "alarms.summary.total": 6, "alarms.summary.left_unexplained": 0},
                                                 "file_lacks": {os.path.join(advw, "advice.txt"): ["cut short"]}}),
    ]))
    # the CPU budget: the runs of the last hour have used it, so the next one does not start
    hc = world(os.path.join(root, "cpu"))
    advc = os.path.join(root, "cpu", "advice-state")
    cfgc = cfg_for(hc, advc)
    ledger = os.path.join(advc, "advice-cost.json")

    def spent(*cpu):
        return [{"write_file": ledger, "text": json.dumps({"runs": [{"at": NOW - 600 + 60 * n, "decision": "full", "cpu_s": c, "wall_s": 1.0} for n, c in enumerate(cpu)]})}]
    scs.append(scenario("files-advice-stops-when-an-hour-of-cpu-is-spent", [
        run(hc.args("advise", "--json", "--set", "advice.budget.cpu_s_per_hour=1") + cfgc, {"exit": 0, "json_has": {"budget.decision": "full", "budget.cpu_s_last_hour": 0.4, "alarms.summary.total": 6}}, setup=spent(0.4)),
        run(hc.args("advise", "--json", "--set", "advice.budget.cpu_s_per_hour=1") + cfgc, {
            "exit": 0, "json_has": {"budget.decision": "skipped", "budget.reason": "cpu budget used: 1.3 of 1 cpu-seconds in the last hour", "alarms": None},
            "file_has": {os.path.join(advc, "advice.txt"): ["skipped on purpose: cpu budget used: 1.3 of 1 cpu-seconds in the last hour"]}}, setup=spent(0.7, 0.6)),
        run(hc.args("advise", "--json", "--set", "advice.budget.cpu_s_per_hour=1") + cfgc, {"exit": 0, "json_has": {"budget.decision": "full", "alarms.summary.total": 6}},
            setup=[{"write_file": ledger, "text": json.dumps({"runs": [{"at": NOW - 4000, "decision": "full", "cpu_s": 5.0, "wall_s": 1.0}]})}]),   # an hour has passed: the spend is forgiven
    ]))
    return scs


def build_install(root):
    """install, installed, a second install, advise with live repair on, uninstall: the crontab ends as it began."""
    h = world(root)
    sh = shims(root)
    tab = os.path.join(root, "crontab")
    seed = "MAILTO=\"\"\n*/30 * * * * /usr/bin/true # someone else's line\n"
    write(tab, seed)
    write(tab + ".seed", seed)
    env = {"PATH": os.pathsep.join([sh, os.environ.get("PATH", "")]), "FIXTURE_CRONTAB": tab}
    d = os.path.join(h.home, "infirmary")
    inst = h.args("install", "--home", h.home, "--adapter", ADAPTER, "--label", "box-a", "--scheduler", "cron")   # the default on a Mac is launchd, which would write a real plist
    cfgf = os.path.join(d, "infirmary.json")
    return [scenario("files-advice-install-cron-is-one-line-and-reversible", [
        run(inst + ["--advice-only", "--dry-run"], {"exit": 0, "text_has": ["dry run: nothing written, nothing scheduled", MARK + d],
                                                    "files": {d: False}, "file_equals": {tab: tab + ".seed"}}, env=env, text=True),
        run(inst, {"exit": 2, "files": {d: False}, "file_equals": {tab: tab + ".seed"}}, env=env, text=True),
        run(inst + ["--advice-only", "--set", "mode=repair"], {"exit": 2, "files": {d: False}, "file_equals": {tab: tab + ".seed"}}, env=env, text=True),
        run(inst + ["--advice-only", "--every", "7"], {"exit": 2, "files": {d: False}, "file_equals": {tab: tab + ".seed"}}, env=env, text=True),
        run(inst + ["--advice-only"], {"exit": 0, "text_has": ["installed, advice-only, for " + h.home, "first run: 6 alarms in the window, 3 explained, 1 real, 2 unknown"],
                                       "files": {os.path.join(d, n): True for n in ("advice.txt", "advice.json", "card.json", "infirmary.json", "install.json", "crontab.before")},
                                       "file_has": {tab: ["*/30 * * * * /usr/bin/true # someone else's line", "*/10 * * * * ", " advise --quiet --config " + cfgf, MARK + d]},
                                       "file_count": {tab: {MARK: 1}}, "file_equals": {os.path.join(d, "crontab.before"): tab + ".seed"},
                                       "json_file": {os.path.join(d, "card.json"): {"tool": "stall-explain-box-a", "mode": "advice-only"},
                                                     cfgf: {"adapter": ADAPTER, "adapter_paths": [files_world.ADAPTER_PATHS], "adapter_config.home": h.home, "state_dir": d,
                                                            "advice.label": "box-a", "advice.every_s": 600}},
                                       "file_lacks": {cfgf: ['"mode"']},
                                       "card_valid": [os.path.join(d, "card.json")]}, env=env, text=True),
        run(inst + ["--advice-only", "--every", "15"], {"exit": 0, "file_count": {tab: {MARK: 1, "*/15 * * * * ": 1, "*/10 * * * * ": 0}}}, env=env, text=True),
        run(["installed", "--home", h.home], {"exit": 0, "text_has": ["installed for " + h.home, "the crontab holds the line", "live repair: off", "fresh"]}, env=env, text=True),
        run(["live", "on", "--by", "fixture", "--config", cfgf], {"exit": 0}),
        run(["advise", "--json", "--config", cfgf], {"exit": 0, "json_has": {"mode": "advice-only", "live_repair.enabled": True, "alarms.summary.total": 6},
                                                     "files": {os.path.join(d, "plans"): False}, "file_lacks": {os.path.join(d, "actions.jsonl"): ['"kind":"repair"']},
                                                     "json_file": {os.path.join(d, "doctor.json"): {"repair_mode": "advice-only", "repairs.mode": "advise"}},
                                                     "unchanged_but": [{"path": h.home, "except": [d]}]}),
        run(["installed", "--home", h.home], {"exit": 0, "text_has": ["live repair: ON"]}, env=env, text=True),
        run(["uninstall", "--home", h.home, "--dry-run"], {"exit": 0, "text_has": ["dry run: would remove 1 crontab line(s)"], "file_count": {tab: {MARK: 1}},
                                                           "files": {cfgf: True}}, env=env, text=True),
        run(["uninstall", "--home", h.home], {"exit": 0, "file_equals": {tab: tab + ".seed"}, "text_has": ["left alone (not the install's own): judged.jsonl"],
                                              "files": {os.path.join(d, "judged.jsonl"): True, os.path.join(d, "advice.txt"): False}}, env=env, text=True),
        run(["installed", "--home", h.home], {"exit": 1, "text_has": ["not installed"]}, env=env, text=True),
        run(["uninstall", "--home", h.home], {"exit": 2, "file_equals": {tab: tab + ".seed"}}, env=env, text=True),
    ])]


def build_launchd(root):
    """The macOS form, through a launchctl shim: one LaunchAgent in the user's own GUI domain, loaded, then booted out and removed."""
    h = world(root)
    sh = shims(root)
    user_home = os.path.join(root, "user")
    os.makedirs(user_home, exist_ok=True)
    reg = os.path.join(root, "launchd")
    env = {"PATH": os.pathsep.join([sh, os.environ.get("PATH", "")]), "HOME": user_home, "FIXTURE_LAUNCHD": reg}
    d = os.path.join(h.home, "infirmary")
    agents = os.path.join(user_home, "Library", "LaunchAgents")
    plist = os.path.join(agents, "org.infirmary.advice.*.plist")
    inst = h.args("install", "--home", h.home, "--adapter", ADAPTER, "--advice-only", "--scheduler", "launchd")
    return [scenario("files-advice-install-launchd-is-one-agent-and-reversible", [
        # the agent carries its own PATH (the user's home expanded at install time, `sysctl` in /usr/sbin included) and is a Standard process: launchd gives an
        # agent only /usr/bin:/bin, and a Background one is throttled until the run overruns its tick budget
        run(inst, {"exit": 0, "text_has": ["installed, advice-only", "LaunchAgent org.infirmary.advice."], "file_has": {reg + ".calls": ["bootstrap gui/"], plist: [
            "<key>EnvironmentVariables</key>", "/opt/homebrew/bin:/usr/local/bin:%s:/usr/bin:/bin:/usr/sbin:/sbin" % os.path.join(user_home, ".local", "bin"),
            "<key>ProcessType</key>\n\t<string>Standard</string>"]}, "file_lacks": {plist: ["Background", "~"]},
                   "glob_count": {os.path.join(agents, "org.infirmary.advice.*.plist"): 1}, "files": {os.path.join(d, "advice.txt"): True}}, env=env, text=True),
        run(["installed", "--home", h.home], {"exit": 0, "text_has": ["launchd has org.infirmary.advice."]}, env=env, text=True),
        run(["uninstall", "--home", h.home], {"exit": 0, "file_has": {reg + ".calls": ["bootout gui/"]}, "glob_count": {os.path.join(agents, "*.plist"): 0},
                                              "files": {os.path.join(d, "judged.jsonl"): True, os.path.join(d, "advice.txt"): False}}, env=env, text=True),
    ])]


def build_refusals(root):
    """An install needs a home the adapter accepts and an adapter that has a home."""
    h = world(root)
    sh = shims(root)
    tab = os.path.join(root, "crontab")
    env = {"PATH": os.pathsep.join([sh, os.environ.get("PATH", "")]), "FIXTURE_CRONTAB": tab}
    return [scenario("files-advice-install-refuses-what-it-cannot-serve", [
        run(h.args("install", "--home", os.path.join(root, "nowhere"), "--adapter", ADAPTER, "--advice-only"), {"exit": 2, "files": {tab: False}}, env=env, text=True),
        run(h.args("install", "--home", root, "--adapter", ADAPTER, "--advice-only"), {"exit": 2, "files": {tab: False}}, env=env, text=True),
        run(["install", "--home", h.home, "--advice-only"], {"exit": 2, "files": {tab: False}}, env=env, text=True),
        run(["installed", "--home", h.home], {"exit": 1, "text_has": ["not installed"]}, env=env, text=True),
    ])]


def main():
    out = os.path.realpath(sys.argv[1])   # install records the home's real path (a Mac's temp directory is a symlink), so the expected paths are real too
    os.makedirs(out, exist_ok=True)
    scs = []
    for name, fn in (("window", build_window), ("rotated", build_rotated), ("install", build_install), ("launchd", build_launchd), ("refusals", build_refusals), ("budget", build_budget)):
        scs += fn(os.path.join(out, name))
    fixlib.save(out, scs)
    print("wrote %d scenarios under %s" % (len(scs), out))


if __name__ == "__main__":
    main()
