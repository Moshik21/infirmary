"""`infirmary install | uninstall | installed --home HOME`: the periodic advice run for one home, as one user-level entry.

Install writes everything into one directory of the home (the adapter names it; `<home>/infirmary` otherwise) and adds one
entry to the invoking user's own scheduler:

    Linux, WSL   one line in the user's crontab, ending in the marker `# infirmary advice <dir>`; every other line is kept
                 byte for byte (a copy of the crontab as it was is kept in <dir>/crontab.before)
    macOS        one LaunchAgent, ~/Library/LaunchAgents/org.infirmary.advice.<hash>.plist, loaded into the user's own GUI domain; it carries its own
                 PATH (Homebrew, ~/.local/bin and the system directories, `sysctl` included) and runs as a Standard process, never Background

Nothing system-wide, no service restarted, no shared configuration touched, no `sudo`. The entry runs `infirmary advise` with
the directory's own infirmary.json, which is always advice-only: the install has no repair option, writes no live-repair record,
and the advice run never repairs even if an operator later turns live repair on for the home. `uninstall` removes the entry and
the directory's known files; `installed` says whether the entry is in place and the advice fresh.
"""
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import sys
import time

from infirmary import VERSION
from infirmary.core import execute, live

MARK = "# infirmary advice "
LAUNCHD_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
CRON_MINUTES = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)
OWN_FILES = ("advice.json", "advice.txt", "card.json", "advice-cost.json", "advise.log", "install.json", "infirmary.json", "crontab.before", "actions.jsonl",
             "actions.jsonl.1", "doctor.json", "seen.json", "disk-history.json", "repairs.json", "repair.lock", "push.seq", "push-doctor.seq",
             "live-repair.json", "off", "plans", "incidents.json")   # not incidents.jsonl or judged.jsonl: the append-only ledgers are records, never deleted by an uninstall
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class InstallError(Exception):
    pass


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def advice_dir(home, adapter, cfg):
    """<home>/<the adapter's ADVICE_DIR>, or <home>/infirmary. The adapter is loaded against the home, so a wrong home fails here."""
    from infirmary.explain import sources
    try:
        src = sources.load(cfg)
    except sources.SourceError as exc:
        raise InstallError("the %s adapter does not accept %s as a home: %s" % (adapter, home, exc))
    return os.path.join(home, getattr(src, "ADVICE_DIR", "infirmary"))


def scheduler_for(name):
    if name in (None, "auto"):
        return "launchd" if sys.platform == "darwin" else "cron"
    if name not in ("cron", "launchd"):
        raise InstallError("--scheduler takes cron or launchd, not %r" % name)
    return name


def command(cfg_path):
    """The argv the entry runs: this Python, this checkout's bin/infirmary, `advise` with the directory's own config."""
    return [sys.executable, os.path.join(REPO, "bin", "infirmary"), "advise", "--quiet", "--config", cfg_path]


def cron_line(argv, every_s, d):
    minutes = every_s // 60
    if every_s % 60 or minutes not in CRON_MINUTES:
        raise InstallError("--every for cron must be one of %s minutes" % ", ".join(str(m) for m in CRON_MINUTES))
    for word in argv + [d]:
        if "%" in word or "\n" in word:
            raise InstallError("a path holds %% or a newline, which a crontab line cannot carry: %r" % word)
    when = "0 * * * *" if minutes == 60 else "*/%d * * * *" % minutes
    return "%s %s > %s 2>&1 %s%s" % (when, " ".join(shlex.quote(w) for w in argv), shlex.quote(os.path.join(d, "advise.log")), MARK, d)


def launchd_label(d):
    return "org.infirmary.advice." + hashlib.sha1(os.path.realpath(d).encode("utf-8")).hexdigest()[:12]


def plist_path(label):
    return os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", label + ".plist")


def _domain():
    return "gui/%d" % os.getuid()


# ---- the user's crontab ---------------------------------------------------------------------------------------------------------
def read_crontab():
    r = execute.run(["crontab", "-l"], timeout=10)
    if r.missing:
        raise InstallError("crontab is not installed here; use --scheduler launchd on macOS, or install cron")
    if r.rc != 0:
        if "no crontab" in (r.err + r.out).lower():
            return ""
        raise InstallError("crontab -l failed (%s): %s" % (r.rc, (r.err or r.out).strip()[:200]))
    if len(r.out) >= execute.MAX_OUT:
        raise InstallError("the crontab is larger than this tool reads whole; refusing to rewrite it")
    return r.out


def _without(text, d):
    keep = [ln for ln in text.splitlines(True) if not ln.rstrip("\n").endswith(MARK + d)]
    return "".join(keep), len(text.splitlines()) - len(keep)


def write_crontab(text):
    r = execute.run(["crontab", "-"], timeout=10, input_text=text)
    if not r.ok:
        raise InstallError("crontab - failed (%s): %s" % (r.rc, (r.err or r.out).strip()[:200]))


# ---- install --------------------------------------------------------------------------------------------------------------------
def _set_dotted(doc, dotted, value):
    node = doc
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
        if not isinstance(node, dict):
            raise InstallError("--set %s: %s is not a section" % (dotted, p))
    node[parts[-1]] = value


def build_config(base_path, sets, home, adapter, d, label, every_s, window_s, card):
    from infirmary.core import config
    doc = config._read(base_path) if base_path else {}
    for item in sets or []:
        name, eq, text = item.partition("=")
        if not eq:
            raise InstallError("--set needs name=value, got %r" % item)
        _set_dotted(doc, name, config.parse_value(text))
    doc["adapter"] = adapter
    if isinstance(doc.get("adapter_paths"), list):     # the scheduler runs from its own working directory: a relative entry would not find the adapter there
        doc["adapter_paths"] = [os.path.abspath(os.path.expanduser(p)) if isinstance(p, str) else p for p in doc["adapter_paths"]]
    doc.setdefault("adapter_config", {})["home"] = home
    doc["state_dir"] = d
    adv = doc.setdefault("advice", {})
    adv.update(every_s=every_s, window_s=window_s, label=label, card=os.path.abspath(os.path.expanduser(card)) if card else None)
    if doc.get("mode") not in (None, "advise"):
        raise InstallError("the install is advice-only; it does not take mode=%r" % doc.get("mode"))
    doc.pop("mode", None)
    return doc


def plan(args, base_path, sets):
    """Everything install would do, computed and checked, nothing written: {dir, config, entry, ...}."""
    from infirmary.core import config
    if not args.advice_only:
        raise InstallError("install needs --advice-only: it is the only kind of install there is (live repair is the operator's `infirmary live on`, never an install step)")
    if not args.home:
        raise InstallError("install needs --home HOME")
    home = os.path.realpath(os.path.expanduser(args.home))
    if not os.path.isdir(home):
        raise InstallError("no such home directory: %s" % home)
    probe = config.load(base_path, list(sets or []) + (["adapter=%s" % args.adapter] if args.adapter else []) + ["adapter_config.home=%s" % json.dumps(home)])
    adapter = probe["adapter"]
    if adapter in (None, "", "files"):
        raise InstallError("install needs an adapter that knows a home (--adapter NAME)")
    d = advice_dir(home, adapter, probe)
    every_s = int(args.every) * 60
    window_s = int(args.window_h) * 3600
    if args.label is not None and not re.match(r"^[a-z0-9][a-z0-9._-]{0,30}$", args.label):
        raise InstallError("--label must be lower-case letters, digits, '.', '_' or '-' (at most 31): it names the card")
    if not 1 <= args.window_h <= 168:
        raise InstallError("--window-hours must be between 1 and 168")
    cfg_doc = build_config(base_path, sets, home, adapter, d, args.label, every_s, window_s, args.card)
    sched = scheduler_for(args.scheduler)
    cfg_path = os.path.join(d, "infirmary.json")
    argv = command(cfg_path)
    out = {"home": home, "dir": d, "config_path": cfg_path, "config": cfg_doc, "scheduler": sched, "argv": argv, "every_s": every_s,
           "advice": {"json": os.path.join(d, "advice.json"), "text": os.path.join(d, "advice.txt"), "card": os.path.join(d, "card.json")}}
    if sched == "cron":
        out["entry"] = {"line": cron_line(argv, every_s, d)}
    else:
        if every_s < 60:
            raise InstallError("--every must be at least 1 minute")
        label = launchd_label(d)
        out["entry"] = {"label": label, "plist": plist_path(label), "domain": _domain(), "path": launchd_path()}
    return out


def launchd_path():
    """The PATH the LaunchAgent runs with, `~` expanded to the installing user's home now. launchd gives an agent only /usr/bin:/bin, which lacks the
    pane-inventory tool (Homebrew, ~/.local/bin) and `sysctl` (/usr/sbin): the boot time then cannot be read and the checks that need it say unknown."""
    return os.pathsep.join(os.path.expanduser(d) for d in LAUNCHD_PATH)


def _plist(p):
    """ProcessType is Standard on purpose: Background makes launchd throttle the agent's CPU and I/O, and a run that takes half a second by hand then
    overran the tick budget before its first pane-list call (docs/advice.md, "Scheduled runs on a Mac")."""
    return {"Label": p["entry"]["label"], "ProgramArguments": p["argv"], "StartInterval": p["every_s"], "RunAtLoad": True,
            "StandardOutPath": os.path.join(p["dir"], "advise.log"), "StandardErrorPath": os.path.join(p["dir"], "advise.log"),
            "EnvironmentVariables": {"PATH": p["entry"]["path"]}, "ProcessType": "Standard", "LowPriorityIO": True}


def install(p, by):
    """Carry out a plan. Idempotent: an entry this directory already has is replaced, never doubled."""
    from infirmary.core import config
    d = p["dir"]
    os.makedirs(d, exist_ok=True)
    tmp = p["config_path"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(p["config"], fh, indent=2, sort_keys=True)
        fh.write("\n")
    try:
        config.load(tmp)            # the file the entry will run with must load, or nothing is scheduled
    except config.ConfigError as exc:
        os.unlink(tmp)
        raise InstallError("the config this install would write does not load: %s" % exc)
    os.replace(tmp, p["config_path"])
    if p["scheduler"] == "cron":
        before = read_crontab()
        with open(os.path.join(d, "crontab.before"), "w", encoding="utf-8") as fh:
            fh.write(before)
        rest, _ = _without(before, d)
        if rest and not rest.endswith("\n"):
            rest += "\n"
        write_crontab(rest + p["entry"]["line"] + "\n")
    else:
        e = p["entry"]
        os.makedirs(os.path.dirname(e["plist"]), exist_ok=True)
        with open(e["plist"], "wb") as fh:
            plistlib.dump(_plist(p), fh)
        execute.run(["launchctl", "bootout", "%s/%s" % (e["domain"], e["label"])], timeout=15)   # a reinstall replaces the old one
        r = execute.run(["launchctl", "bootstrap", e["domain"], e["plist"]], timeout=15)
        if not r.ok:
            raise InstallError("launchctl bootstrap failed (%s): %s" % (r.rc, (r.err or r.out).strip()[:200]))
    rec = {"schema": "infirmary.install", "schema_version": "1.0", "tool_version": VERSION, "installed_at": _iso(time.time()), "by": by,
           "home": p["home"], "dir": d, "config": p["config_path"], "scheduler": p["scheduler"], "entry": p["entry"], "argv": p["argv"],
           "every_s": p["every_s"], "mode": live.ADVICE_ONLY}
    with open(os.path.join(d, "install.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return rec


# ---- uninstall and status -------------------------------------------------------------------------------------------------------
def find(home):
    """The install record for a home: (dir, record) or (None, None). It is looked for at most two directory levels below the home,
    so uninstall and status need neither the adapter nor the options the install was given."""
    home = os.path.realpath(os.path.expanduser(home))
    level = [home]
    for depth in range(3):
        nxt = []
        for base in level:
            path = os.path.join(base, "install.json")
            try:
                with open(path, encoding="utf-8") as fh:
                    rec = json.load(fh)
                if isinstance(rec, dict) and rec.get("schema") == "infirmary.install" and rec.get("home") == home and rec.get("dir") == base:
                    return base, rec
            except (OSError, ValueError):
                pass
            if depth == 2:
                continue
            try:
                nxt += [e.path for e in os.scandir(base) if e.is_dir(follow_symlinks=False) and not e.name.endswith(".inbox")]
            except OSError:
                pass
        level = nxt
    return None, None


def uninstall(home, dry_run=False, keep_files=False):
    """Remove the entry and the directory's own files. -> {dir, removed_entry, removed_files, left}."""
    d, rec = find(home)
    if rec is None:
        raise InstallError("no infirmary install found under %s (nothing to remove)" % os.path.realpath(os.path.expanduser(home)))
    out = {"dir": d, "scheduler": rec["scheduler"], "removed_entry": None, "removed_files": [], "left": [], "dry_run": dry_run}
    if rec["scheduler"] == "cron":
        cur = read_crontab()
        rest, n = _without(cur, d)
        out["removed_entry"] = "%d crontab line(s) ending in %s%s" % (n, MARK, d)
        if n and not dry_run:
            write_crontab(rest)
    else:
        e = rec["entry"]
        out["removed_entry"] = "LaunchAgent %s (%s)" % (e["label"], e["plist"])
        if not dry_run:
            execute.run(["launchctl", "bootout", "%s/%s" % (e["domain"], e["label"])], timeout=15)
            try:
                os.unlink(e["plist"])
            except FileNotFoundError:
                pass
    if keep_files:
        names = [n for n in ("install.json",) if os.path.exists(os.path.join(d, n))]
    else:
        names = sorted(os.listdir(d))
    for n in names:
        p = os.path.join(d, n)
        if n in OWN_FILES or (n.startswith(".") and n.endswith(".tmp")):
            out["removed_files"].append(n)
            if not dry_run:
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.unlink(p)
        else:
            out["left"].append(n)
    if not dry_run and not keep_files and not out["left"]:
        try:
            os.rmdir(d)
        except OSError:
            pass
    return out


def status(home, now=None):
    """{installed, entry_present, fresh, ...}: is the entry in place, and is the advice newer than three intervals?"""
    now = now or time.time()
    d, rec = find(home)
    if rec is None:
        return {"installed": False, "why": "no infirmary install found under %s" % os.path.realpath(os.path.expanduser(home))}
    out = {"installed": True, "dir": d, "scheduler": rec["scheduler"], "entry": rec["entry"], "installed_at": rec["installed_at"], "by": rec.get("by"),
           "config": rec["config"], "mode": live.ADVICE_ONLY, "every_s": rec["every_s"]}
    if rec["scheduler"] == "cron":
        try:
            lines = read_crontab().splitlines()
            out["entry_present"] = rec["entry"]["line"] in lines
            out["entry_why"] = "the crontab holds the line" if out["entry_present"] else (
                "the crontab holds a different line for this directory" if any(l.endswith(MARK + d) for l in lines) else "the crontab has no line for this directory")
        except InstallError as exc:
            out["entry_present"], out["entry_why"] = False, str(exc)
    else:
        e = rec["entry"]
        r = execute.run(["launchctl", "print", "%s/%s" % (e["domain"], e["label"])], timeout=15)
        out["entry_present"] = r.ok and os.path.exists(e["plist"])
        out["entry_why"] = "launchd has %s loaded" % e["label"] if out["entry_present"] else "launchd does not have %s loaded (%s)" % (e["label"], (r.err or r.out).strip()[:120])
    try:
        with open(os.path.join(d, "advice.json"), encoding="utf-8") as fh:
            adv = json.load(fh)
        out["advice_age_s"] = int(now - adv["at_epoch"])
        out["fresh"] = out["advice_age_s"] <= 3 * rec["every_s"] + 60
        s = (adv.get("alarms") or {}).get("summary") or {}
        out["advice"] = {"written_at": adv["written_at"], "alarms": s.get("total"), "explained": s.get("explained"), "real": s.get("real"),
                         "unknown": s.get("unknown"), "open_lanes": s.get("open_lanes"), "doctor": (adv.get("doctor") or {}).get("status")}
    except (OSError, ValueError, KeyError):
        out["fresh"], out["advice_age_s"] = False, None
    try:
        with open(os.path.join(d, "advise.log"), encoding="utf-8", errors="replace") as fh:
            out["last_log"] = fh.read()[-600:].strip()
    except OSError:
        out["last_log"] = None
    from infirmary.core import config
    try:
        cfg = config.load(rec["config"])
        lv = live.read(cfg)
        out["live_repair"] = {"enabled": lv["on"], "why": lv["why"]}
    except config.ConfigError as exc:
        out["live_repair"] = {"enabled": None, "why": "the installed config does not load: %s" % exc}
    return out
