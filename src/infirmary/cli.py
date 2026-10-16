"""infirmary: the Infirmary's front door. It detects, explains and reports; with the operator's `live on` for a home it also runs the safe repairs.

    infirmary check [-v] [--json]   run the catalogue once: one line per area, each finding with its why and its how;
                                       with live repair on also run the tier-1 repairs and write the tier-2 plans
    infirmary plan [FINDING_ID ..]  write a plan for every repair the current findings call for (any tier, any mode)
    infirmary plans [--json]        the plans, with their state
    infirmary approve PLAN_ID       re-verify a plan and run exactly its command once (a person's word: --by NAME)
    infirmary reset ACTION          close an action's circuit breaker after a person looked at why it tripped
    infirmary status [--json]       mode, kill switch, host identity, the host beat, the last check, breakers and plans
    infirmary off | on              the kill switch (a file in the state directory); INFIRMARY=off does the same
    infirmary live [status]         whether live repair is on for this home (the default is off: advice-only)
    infirmary live on | off [--by NAME]
                                       the operator's switch for live repair on this home; recorded with who and when. Off by
                                       default; nothing but this command turns it on. The kill switch wins over it
    infirmary card [--card PATH]    the Infirmary's state as one status card (lookout.card 1.0) from the last check; prints it, or
                                       writes PATH (a file, or a directory: PATH/infirmary.json). Runs no check, writes nothing else
    infirmary advise [--json]       the periodic advice-only run: the check (never repairing, whatever the toggle says) and stall-explain
                                       over the alarms of the last 24 h; writes advice.json, advice.txt and card.json in the state directory.
                                       It keeps a wall-time and a CPU budget and steps aside, saying so, when the machine is loaded or on battery
    infirmary judged [--days N] [--json]
                                       what the advise run's explanations came to: alarms explained, called real, wrong-benign, wrong-real and
                                       undecided, counted from judged.jsonl. Reads that file only; exit 1 when there is nothing to count
    infirmary install --home HOME --adapter NAME --advice-only [--label NAME] [--every MINUTES] [--card DIR] [--dry-run]
                                       schedule `advise` for one home as one user-level entry (a crontab line; a LaunchAgent on macOS);
                                       its files live in the home. Nothing system-wide, nothing restarted, no repair option
    infirmary installed --home HOME is it installed for that home, and is the advice fresh? (exit 0 yes, 1 no)
    infirmary uninstall --home HOME [--dry-run] [--keep-files]
                                       remove the entry and the install's files; every other crontab line is left as it was
    infirmary version

    check options: --only ID|N[,..]  --push (Lookout)  --dry-runs (also run the owners' dry runs)  --no-write
                   --card PATH  (also write the status card; off unless given)  --stale-after SECONDS  (card: how long it stays current, 900)
                   --watch SECONDS [--ticks N]  (repeat; hysteresis needs more than one look)
    mode: advice-only unless `infirmary live on` was run for this home; `mode` in the config or INFIRMARY_MODE can only be
          advise or off, never repair

Exit status of `check` is the worst finding: 0 all clear, 10 warning, 30 critical, 50 could not decide; 2 for a usage error,
3 for an internal error. `approve` exits 0 when the repair was verified or the handoff recorded, 1 when it was refused or failed.
"""
import argparse
import getpass
import json
import os
import sys
import time

from infirmary import VERSION
from infirmary.core import actions, beat, config, hostid, live, push, state, streams

VERBS = ["status", "off", "on", "live", "check", "card", "version", "plan", "plans", "approve", "reset", "advise", "judged", "install", "uninstall", "installed"]


def main(argv=None):
    streams.safe_output()
    ap = argparse.ArgumentParser(prog="infirmary", description=__doc__.split("\n")[0], epilog="\n".join(__doc__.split("\n")[2:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="infirmary " + VERSION)
    ap.add_argument("command", choices=VERBS)
    ap.add_argument("target", nargs="*", help="plan: finding ids; approve: a plan id; reset: an action name")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="check: print the evidence behind each finding")
    ap.add_argument("--config")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--only", help="check: run only these checks (ids or catalogue numbers, comma separated)")
    ap.add_argument("--push", action="store_true", help="check: also push the findings to Lookout (needs lookout.enabled)")
    ap.add_argument("--card", metavar="PATH", help="check: also write the status card (lookout.card 1.0) to PATH, a file or a directory; card: write it there instead of printing it")
    ap.add_argument("--stale-after", type=int, default=900, metavar="SECONDS", help="card: seconds after the check the card may be called current (60-604800, default 900)")
    ap.add_argument("--dry-runs", action="store_true", help="check: run the owning scripts' dry runs and attach their output")
    ap.add_argument("--no-write", action="store_true", help="check: write nothing, not even doctor.json or the Infirmary's own memory of what it saw (and act on nothing)")
    ap.add_argument("--watch", type=float, metavar="SECONDS", help="check: look again every SECONDS (stop with ctrl-c)")
    ap.add_argument("--ticks", type=int, help="check --watch: stop after this many looks")
    ap.add_argument("--by", help="approve, reset: who decided (default: this user)")
    ap.add_argument("--home", help="install, uninstall, installed: the home the advice run is for")
    ap.add_argument("--adapter", help="install: the adapter that reads the home")
    ap.add_argument("--advice-only", action="store_true", help="install: required; the only kind of install there is")
    ap.add_argument("--label", help="install: names this home's card on a shared board (stall-explain-LABEL)")
    ap.add_argument("--every", type=int, default=10, metavar="MINUTES", help="install: how often the advice runs (default 10)")
    ap.add_argument("--window-hours", dest="window_h", type=int, default=24, metavar="HOURS", help="install: the alarm window (default 24)")
    ap.add_argument("--scheduler", choices=["auto", "cron", "launchd"], default="auto", help="install: cron (Linux, WSL) or launchd (macOS); auto picks")
    ap.add_argument("--dry-run", action="store_true", help="install, uninstall: say what would be done and do nothing")
    ap.add_argument("--keep-files", action="store_true", help="uninstall: remove only the entry and the install record, keep the advice and state")
    ap.add_argument("--quiet", action="store_true", help="advise: print nothing unless something went wrong (what the scheduled entry uses)")
    ap.add_argument("--days", type=int, default=14, metavar="N", help="judged: how many days back to count (default 14)")
    ap.add_argument("--until", type=int, metavar="EPOCH", help="judged: count up to this moment (default: now)")
    args = ap.parse_args(argv)
    try:
        cfg = config.load(args.config, args.set + (["doctor.dry_runs=true"] if args.dry_runs else []))
    except config.ConfigError as exc:
        print("infirmary: %s" % exc, file=sys.stderr)
        return 2
    sd = state.state_dir(cfg)
    live.apply(cfg)
    if args.command == "version":
        print("infirmary " + VERSION)
        return 0
    if args.command in ("off", "on"):
        state.set_off(cfg, args.command == "off")
        actions.record(sd, "infirmary", "switch", cfg["mode"], to=args.command)
        print("infirmary is now %s (%s)" % (args.command, state.switch_file(cfg)))
        return 0
    if args.command in ("install", "uninstall", "installed"):
        return _install(args, cfg)
    if args.command in ("check", "card", "plan", "plans", "approve", "reset", "live", "advise", "judged"):
        try:
            return VERB_FN[args.command](args, cfg, sd)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:   # never a stack trace for a person who asked one question
            print("infirmary: internal error: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            if os.environ.get("INFIRMARY_DEBUG"):
                raise
            return 3
    return _status(args, cfg, sd)


def _status(args, cfg, sd):
    from infirmary.doctor import repair
    b = beat.read(cfg["beat"]["path"] or beat.default_path())
    out = cfg["doctor"]["out"] or os.path.join(sd, "doctor.json")
    last = None
    try:
        with open(os.path.expanduser(out), encoding="utf-8") as fh:
            d = json.load(fh)
        last = {"path": out, "checked_at": d.get("checked_at"), "status": d.get("status"), "age_s": int(time.time() - os.stat(os.path.expanduser(out)).st_mtime),
                "findings": len(d.get("findings") or [])}
    except (OSError, ValueError):
        pass
    h = repair.load_history(sd)
    breakers = dict((n, repair.breaker_state(h, n, cfg["doctor"]["repair"], time.time())) for n in sorted(h["breakers"]))
    doc = {"version": VERSION, "mode": cfg["mode"], "repair_mode": live.label(cfg), "live_repair": live.summary(cfg), "off": state.is_off(cfg), "state_dir": sd, "host": hostid.identity(cfg),
           "beat": b, "doctor_json": last, "config": cfg.get("_loaded_from"), "breakers": breakers, "plans": repair.open_plans(sd),
           "repairs_last_hour": len([r for r in h["runs"] if time.time() - r["ts"] < 3600 and r["outcome"] in repair.RAN])}
    if args.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
        return 0
    print("infirmary %s · mode %s%s · host %s (%s)" % (VERSION, doc["repair_mode"], " (OFF: kill switch)" if doc["off"] else "", doc["host"]["id"], doc["host"]["os"]))
    print(_live_line(cfg))
    print("state dir: %s" % sd)
    print("host beat: %s" % ("level %s, %ds old" % (b["level"], b["age_s"]) if b else "not found (run lookout-beat, or set beat.path)"))
    print("last check: %s" % ("%s, %s, %d finding(s), %ds ago" % (last["checked_at"], last["status"], last["findings"], last["age_s"]) if last else "none yet (run `infirmary check`)"))
    tripped = [n for n, s in breakers.items() if s["tripped"]]
    print("repairs: %d in the last hour; breakers tripped: %s" % (doc["repairs_last_hour"], ", ".join(tripped) or "none"))
    print("plans waiting for approval: %s" % (", ".join(p["id"] for p in doc["plans"]) or "none"))
    return 0


def _live_line(cfg):
    lv = cfg["_live"]
    if lv["on"]:
        return "live repair: ON · %s · the kill switch, the breakers, flap detection and the rate limits still apply%s" % (
            lv["why"], "; the kill switch is on, so nothing acts" if state.is_off(cfg) else "")
    return "live repair: OFF · %s · advice-only: findings and plans, nothing is acted on (`infirmary live on` is the operator's switch)" % lv["why"]


def _live(args, cfg, sd):
    sub = args.target[0] if args.target else "status"
    if len(args.target) > 1 or sub not in ("on", "off", "status"):
        print("infirmary: live takes on, off or status", file=sys.stderr)
        return 2
    if sub != "status":
        by = args.by or _who()
        was = cfg["_live"]["on"]
        try:
            live.turn(cfg, sub == "on", by)
        except OSError as exc:
            print("infirmary: cannot record the choice in %s: %s; live repair stays as it was" % (live.path(cfg), exc), file=sys.stderr)
            return 1
        live.apply(cfg)
        actions.record(sd, "infirmary", "live-repair", cfg["mode"], to=sub, was="on" if was else "off", by=by, scope=live.scope(cfg))
    if args.json:
        print(json.dumps({"repair_mode": live.label(cfg), "live_repair": live.summary(cfg), "off": state.is_off(cfg), "scope": live.scope(cfg), "file": live.path(cfg)},
                         indent=2, sort_keys=True))
    else:
        print(_live_line(cfg))
    return 0


def _engine(cfg, sd, loaded):
    from infirmary.doctor import repair, runner
    from infirmary.doctor.context import Ctx
    ctx = Ctx(cfg)
    ctx.adapter = loaded[0]
    return repair.Engine(cfg, loaded, sd, ctx, lambda ids: runner.redetect(cfg, loaded, ids))


def _check(args, cfg, sd):
    from infirmary.doctor import feed, render, runner
    if state.is_off(cfg):
        print("infirmary is off (kill switch); nothing was checked")
        return 0
    only = set(x.strip() for x in args.only.split(",")) if args.only else None
    n, code = 0, 0
    loaded = runner.load(cfg)
    run_cfg = dict(cfg, mode="advise") if args.no_write and cfg["mode"] == "repair" else cfg   # --no-write acts on nothing
    while True:
        doc = runner.run(run_cfg, only=only, persist=not args.no_write, loaded=loaded)
        t0 = time.time()
        doc["repairs"] = _engine(run_cfg, sd, loaded).tick(doc)
        doc["cost"]["repairs_ms"] = int((time.time() - t0) * 1000)
        if not args.no_write:
            runner.write_doctor_json(doc, cfg)
            actions.record(sd, "infirmary", "check", cfg["mode"], status=doc["status"], findings=[f["id"] for f in doc["findings"] if not f["pending"]],
                           took_ms=doc["cost"]["took_ms"], dry_runs=doc["dry_runs_run"],
                           repairs=[[o["action"], o["outcome"]] for o in doc["repairs"]["outcomes"]])
        if args.card:
            from infirmary.doctor import card
            try:
                print("card: wrote %s" % card.write(args.card, card.build(doc, cfg, _stale_after(args))), file=sys.stderr)
            except OSError as exc:
                print("card: not written: %s" % exc, file=sys.stderr)
        if args.push:
            ok, text = push.deliver(lambda seq, boot: feed.build(doc, cfg, seq, boot), cfg, sd, cfg["doctor"]["lookout_source_id"], "push-doctor.seq")
            print("lookout push: %s" % ("ok" if ok else "failed: " + text), file=sys.stderr)
        print(json.dumps(doc, indent=2, sort_keys=True) if args.json else render.text(doc, args.verbose))
        code = doc["exit_code"]
        n += 1
        if not args.watch or (args.ticks and n >= args.ticks):
            return code
        sys.stdout.flush()
        time.sleep(args.watch)


def _stale_after(args):
    return min(max(args.stale_after, 60), 604800)


def _card(args, cfg, sd):
    """The card for the last check (doctor.json): no check runs and nothing but PATH is written."""
    from infirmary.doctor import card
    out = os.path.expanduser(cfg["doctor"]["out"] or os.path.join(sd, "doctor.json"))
    try:
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["checked_at"], doc["findings"], doc["status"]
    except (OSError, ValueError, KeyError):
        print("infirmary: no readable check in %s yet (run `infirmary check`); no card" % out, file=sys.stderr)
        return 1
    c = card.build(doc, cfg, _stale_after(args))
    if args.card:
        print("card: wrote %s" % card.write(args.card, c), file=sys.stderr)
    else:
        print(json.dumps(c, indent=2, sort_keys=True))
    return 0


def _plan(args, cfg, sd):
    from infirmary.doctor import render, runner
    if state.is_off(cfg):
        print("infirmary is off (kill switch); nothing was planned")
        return 0
    loaded = runner.load(cfg)
    doc = runner.run(cfg, persist=True, loaded=loaded)
    rep = _engine(cfg, sd, loaded).tick(doc, write_plans_only=True, select=args.target or None)
    if args.json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(render.repairs_text(rep) or "no repair applies to the current findings%s" % (" named" if args.target else ""))
    return 0


def _plans(args, cfg, sd):
    from infirmary.doctor import render, repair
    ps = repair.all_plans(sd)
    if args.json:
        print(json.dumps(ps, indent=2, sort_keys=True))
        return 0
    if not ps:
        print("no plans in %s" % repair.plans_dir(sd))
    for p in ps:
        print(render.plan_line(p))
    return 0


def _approve(args, cfg, sd):
    from infirmary.doctor import repair, runner
    from infirmary.doctor.context import Ctx
    if len(args.target) != 1:
        print("infirmary: approve takes exactly one plan id (see `infirmary plans`)", file=sys.stderr)
        return 2
    loaded = runner.load(cfg)
    ctx = Ctx(cfg)
    ctx.adapter = loaded[0]
    out = repair.approve(cfg, loaded, sd, ctx, lambda ids: runner.redetect(cfg, loaded, ids), args.target[0], args.by or _who())
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print("%s · %s · %s" % (out["outcome"], out.get("plan"), out["reason"]))
        for line in out.get("output") or []:
            print("  " + line)
    return 0 if out["outcome"] in ("verified", "approved") else 1


def _reset(args, cfg, sd):
    from infirmary.doctor import repair
    if len(args.target) != 1:
        print("infirmary: reset takes exactly one action name", file=sys.stderr)
        return 2
    was = repair.reset(cfg, sd, args.target[0], args.by or _who())
    print("breaker for %s closed (it was %s, %d failure(s) in a row)" % (args.target[0], "tripped" if was["tripped"] else "not tripped", was["failures"]))
    return 0


def _advise(args, cfg, sd):
    """The advice-only run: check, explain the window's alarms, write the advice. Repairs nothing, whatever the toggle says."""
    from infirmary.doctor import advice
    if args.card:
        cfg["advice"]["card"] = args.card
    doc = advice.build(cfg, persist=not args.no_write)
    if args.no_write:
        print(json.dumps(doc, indent=1, sort_keys=True) if args.json else advice.text(doc), end="" if not args.json else "\n")
        return 0
    wrote, problems = advice.write(doc, cfg, advice.stale_after(cfg))
    if args.json:
        print(json.dumps(doc, indent=1, sort_keys=True))
    elif not args.quiet:
        print(advice.text(doc), end="")
        print("wrote: %s" % ", ".join(wrote))
    for p in problems:
        print("infirmary: advice not written: %s" % p, file=sys.stderr)
    return 1 if problems else 0


def _judged(args, cfg, sd):
    """The judged alarms in a window, counted from the ledger. Reads judged.jsonl and nothing else; writes nothing, not even an actions row."""
    from infirmary.doctor import judged
    if args.days < 1:
        print("infirmary: --days must be at least 1", file=sys.stderr)
        return 2
    until = args.until if args.until is not None else int(time.time())
    rows, skipped, problem = judged.read(sd)
    doc = judged.report(rows, skipped, problem, until - args.days * 86400, until, int(time.time()) if args.until is None else until)
    print(json.dumps(doc, indent=1, sort_keys=True) if args.json else judged.text(doc), end="" if not args.json else "\n")
    return 0 if doc["counts"] is not None else 1


def _install(args, cfg):
    from infirmary.doctor import install
    try:
        if args.command == "install":
            p = install.plan(args, args.config, args.set)
            entry = p["entry"].get("line") or "LaunchAgent %s at %s (every %ds)" % (p["entry"]["label"], p["entry"]["plist"], p["every_s"])
            if args.dry_run:
                if args.json:
                    print(json.dumps(p, indent=2, sort_keys=True))
                else:
                    print("dry run: nothing written, nothing scheduled")
                    print("would write %s:\n%s" % (p["config_path"], json.dumps(p["config"], indent=2, sort_keys=True)))
                    print("would add (%s): %s" % (p["scheduler"], entry))
                    print("advice: %s, %s; card: %s%s" % (p["advice"]["text"], p["advice"]["json"], p["advice"]["card"],
                                                          " and %s" % p["config"]["advice"]["card"] if p["config"]["advice"]["card"] else ""))
                return 0
            rec = install.install(p, args.by or _who())
            first = _first_advice(p["config_path"])
            if args.json:
                print(json.dumps(dict(rec, first_run=first), indent=2, sort_keys=True))
            else:
                print("installed, advice-only, for %s" % p["home"])
                print("  entry (%s): %s" % (p["scheduler"], entry))
                print("  config: %s" % p["config_path"])
                print("  advice: %s (and advice.json, card.json beside it)" % p["advice"]["text"])
                print("  first run: %s" % first)
                print("  check it: infirmary installed --home %s" % p["home"])
                print("  undo it:  infirmary uninstall --home %s" % p["home"])
            return 0
        if args.command == "uninstall":
            if not args.home:
                raise install.InstallError("uninstall needs --home HOME")
            out = install.uninstall(args.home, dry_run=args.dry_run, keep_files=args.keep_files)
            if args.json:
                print(json.dumps(out, indent=2, sort_keys=True))
            else:
                print("%s%s" % ("dry run: would remove " if args.dry_run else "removed ", out["removed_entry"]))
                print("%s %d file(s) in %s%s" % ("would remove" if args.dry_run else "removed", len(out["removed_files"]), out["dir"],
                                                 "; left alone (not the install's own): %s" % ", ".join(out["left"]) if out["left"] else ""))
            return 0
        if not args.home:
            raise install.InstallError("installed needs --home HOME")
        st = install.status(args.home)
        ok = st["installed"] and st.get("entry_present") and st.get("fresh")
        if args.json:
            print(json.dumps(dict(st, ok=bool(ok)), indent=2, sort_keys=True))
        elif not st["installed"]:
            print("not installed: %s" % st["why"])
        else:
            a = st.get("advice") or {}
            print("installed for %s (%s, every %ds, by %s at %s) · %s" % (args.home, st["scheduler"], st["every_s"], st["by"], st["installed_at"], st["mode"]))
            print("  entry: %s" % st["entry_why"])
            print("  advice: %s" % ("%s, %ds old (%s): %s alarms, %s explained, %s real, %s unknown, open now: %s; doctor %s" % (
                os.path.join(st["dir"], "advice.txt"), st["advice_age_s"], "fresh" if st["fresh"] else "STALE", a.get("alarms"), a.get("explained"),
                a.get("real"), a.get("unknown"), ", ".join(a.get("open_lanes") or []) or "none", a.get("doctor")) if st["advice_age_s"] is not None else "none written yet"))
            print("  live repair: %s (%s)" % ("ON" if st["live_repair"]["enabled"] else "off", st["live_repair"]["why"]))
            if st.get("last_log"):
                print("  last run said: %s" % st["last_log"].replace("\n", " | ")[-300:])
        return 0 if ok else 1
    except install.InstallError as exc:
        print("infirmary: %s" % exc, file=sys.stderr)
        return 2


def _first_advice(cfg_path):
    """Run the advice once, now, with the installed config, so the files exist before the first scheduled run."""
    from infirmary.doctor import advice
    try:
        c = config.load(cfg_path)
        live.apply(c)
        doc = advice.build(c)
        wrote, problems = advice.write(doc, c, advice.stale_after(c))
    except Exception as exc:   # the entry is in place either way; the next scheduled run tries again
        return "failed (%s: %s); the scheduled run will try again" % (type(exc).__name__, exc)
    s = (doc.get("alarms") or {}).get("summary")
    bud = doc.get("budget") or {}
    if bud.get("decision") == "skipped":
        return "skipped on purpose: %s; the scheduled run measures again%s" % (bud["reason"], "; problems: %s" % "; ".join(problems) if problems else "")
    return "%s%s" % ("%d alarms in the window, %d explained, %d real, %d unknown" % (s["total"], s["explained"], s["real"], s["unknown"]) if s
                     else "alarms not read (%s)" % (doc.get("alarms") or {}).get("why", "kill switch on"), "; problems: %s" % "; ".join(problems) if problems else "")


def _who():
    try:
        return getpass.getuser()
    except Exception:   # no user name on this OS: say so rather than guess
        return "unknown"


VERB_FN = {"advise": _advise, "judged": _judged, "live": _live, "check": _check, "card": _card, "plan": _plan, "plans": _plans, "approve": _approve, "reset": _reset}
