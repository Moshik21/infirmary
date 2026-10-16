"""stall-explain: why is this lane quiet? One line, with the evidence behind it, in about a second.

    stall-explain LANE                      explain one lane
    stall-explain --window ID               explain by watcher window (adapters that have windows)
    stall-explain --replay FILE             score the engine over recorded alarms
    stall-explain --demo                    replay the bundled synthetic alarm set (no setup)
    stall-explain --export-replay OUT       ask the adapter to write a replay file from its records
    add --json for machines, -v for the evidence, --set name=value to override any setting

Exit status is the verdict: 0 benign, 10 waiting on supervisor, 20 waiting on a human, 30 wedged,
40 dead, 50 unknown; 2 for a usage error, 3 for an internal error.
"""
import argparse
import json
import os
import sys
import time

from infirmary import VERSION
from infirmary.core import actions, config, findings, push, state, streams
from infirmary.explain import engine, evidence, replay, sources

DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synthetic.jsonl")   # package data: the wheel carries it too


def _rss_mb():
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(rss / (1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0), 1)
    except (ImportError, ValueError):
        return None


def build_parser():
    ap = argparse.ArgumentParser(prog="stall-explain", description=__doc__.split("\n")[0], epilog="\n".join(__doc__.split("\n")[2:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lane", nargs="?", help="lane id")
    ap.add_argument("--window", help="watcher window id (adapter-defined)")
    ap.add_argument("--alarm-text", help="the text of the alarm being explained; the adapter turns it into a trigger")
    ap.add_argument("--idle-s", type=int, help="how long the lane has been silent, if the caller knows")
    ap.add_argument("--replay", metavar="FILE")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--export-replay", metavar="OUT")
    ap.add_argument("--list", action="store_true", help="list the lanes the source knows")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the evidence behind the verdict")
    ap.add_argument("--config", help="config file (default: $INFIRMARY_CONFIG, ./infirmary.json, ~/.config/infirmary/config.json)")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override one setting, e.g. thresholds.wedge_min_s=600")
    ap.add_argument("--no-log", action="store_true", help="do not append to the actions log")
    ap.add_argument("--push", action="store_true", help="also push the verdict to Lookout (needs lookout.enabled)")
    ap.add_argument("--gate", action="store_true", help="with --replay: exit 1 unless the accuracy gate passes")
    ap.add_argument("--version", action="version", version="stall-explain " + VERSION)
    return ap


def main(argv=None):
    streams.safe_output()
    ap = build_parser()
    args, rest = ap.parse_known_args(argv)
    adapter_args = {}
    if rest:
        ap.error("unrecognised arguments: %s" % " ".join(rest))
    try:
        cfg = config.load(args.config, args.set)
        if state.is_off(cfg):
            print("stall-explain is off (kill switch); nothing was checked")
            return findings.EXIT_OFF
        if args.demo or args.replay:
            return _replay(args, cfg, args.replay or DEMO)
        src = sources.load(cfg)
        if args.list:
            for lane in src.lanes():
                print(lane["id"] if lane["label"] == lane["id"] else "%s\t%s" % (lane["id"], lane["label"]))
            return 0
        if args.export_replay:
            if not hasattr(src, "export_replay"):
                print("the %s source cannot export replays" % cfg["adapter"], file=sys.stderr)
                return findings.EXIT_USAGE
            n = src.export_replay(args.export_replay, cfg)
            print("wrote %d alarms to %s" % (n, args.export_replay))
            return 0
        if not (args.lane or args.window):
            ap.error("name a lane, or use --window, --replay, --demo or --list")
        return _explain(args, cfg, src)
    except (config.ConfigError, sources.SourceError, replay.ReplayError) as exc:
        print("stall-explain: %s" % exc, file=sys.stderr)
        return findings.EXIT_USAGE
    except Exception as exc:  # never a stack trace for a person who asked one question
        print("stall-explain: internal error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        if os.environ.get("INFIRMARY_DEBUG"):
            raise
        return findings.EXIT_INTERNAL


def _explain(args, cfg, src):
    clock = evidence.Clock(cfg["budget_ms"])
    subject = src.resolve(lane=args.lane, window=args.window)
    if subject is None:
        print("stall-explain: no such lane or window: %s" % (args.lane or args.window), file=sys.stderr)
        return findings.EXIT_USAGE
    trigger = src.parse_alarm(args.alarm_text) if args.alarm_text and hasattr(src, "parse_alarm") else {}
    if args.idle_s is not None:
        trigger = dict(trigger or {}, idle_s=args.idle_s, kind=(trigger or {}).get("kind") or "silence")
    ev = src.collect(subject, clock, trigger=trigger)
    took = clock.elapsed_ms()
    if took > cfg["hard_cap_ms"]:
        ev["sources"].append({"name": "time budget", "status": "timeout", "ms": took})
    v = engine.explain(ev, cfg, took_ms=took)
    if not args.no_log:
        sd = state.state_dir(cfg)
        actions.record(sd, "stall-explain", "explain", cfg["mode"], subject=subject["id"], verdict=v["verdict"], took_ms=took)
    if args.push:
        ok, text = push.push([v], cfg, state.state_dir(cfg))
        print("lookout push: %s" % ("ok" if ok else "failed: " + text), file=sys.stderr)
    if args.json:
        v["cost"] = {"took_ms": took, "rss_mb": _rss_mb(), "over_budget": took > cfg["budget_ms"]}
        print(json.dumps(v, indent=2, sort_keys=True))
    else:
        print(findings.line(v))
        if args.verbose:
            for f in v["evidence"]:
                print("  %s: %s%s" % (f["source"], f["fact"], " (%s)" % f["ref"] if f.get("ref") else ""))
            print("  recommendation: %s" % v["recommendation"])
            print("  took %d ms (budget %d ms)" % (took, cfg["budget_ms"]))
    return v["exit_code"]


def _replay(args, cfg, path):
    header, alarms = replay.load(path)
    results = replay.run(header, alarms, cfg)
    sc = replay.score(header, results)
    if args.json:
        print(json.dumps({"header": {k: header[k] for k in header if k != "classes"}, "score": {k: v for k, v in sc.items() if k != "misses"},
                          "misses": [{"id": r["id"], "class": r["class"], "why": why} for r, why in sc["misses"]],
                          "results": [{"id": r["id"], "class": r["class"], "verdict": r["verdict"]["verdict"], "why": r["verdict"]["why"],
                                       "qualifiers": r["verdict"]["qualifiers"]} for r in results]}, indent=2, sort_keys=True))
    else:
        if args.verbose or args.demo:
            for r in results:
                print("%-6s %-22s %s" % (r["id"], r["class"] or "-", findings.line(r["verdict"])))
            print()
        print(replay.render(header, results, sc))
    if not args.no_log:
        actions.record(state.state_dir(cfg), "stall-explain", "replay", cfg["mode"], file=os.path.basename(path), alarms=len(results), gate=sc["gate"]["passed"])
    return 0 if (sc["gate"]["passed"] or not args.gate) else 1
