"""Configuration: one JSON (or TOML on Python 3.11+) file, every threshold named, `--set` overrides.

Resolution order, last wins: built-in DEFAULTS, the config file, environment (INFIRMARY_MODE: off or advise),
then `--set dotted.name=value` on the command line.
"""
import copy
import json
import os
import re

DEFAULTS = {
    "mode": "advise",                  # off | advise. Repairs are never a setting: `infirmary live on` is the one switch (docs/safety.md)
    "state_dir": None,                 # None = the per-OS default, see core.state
    "budget_ms": 1000,                 # target time for one verdict
    "hard_cap_ms": 2000,               # past this the verdict is `unknown`, never a wait
    "host": {"id": None},              # name this machine; default is the short hostname
    "adapter": "files",                # `files` (built in) or the name of a directory in adapter_paths
    "adapter_paths": [],               # extra directories searched for <name>/source.py
    "thresholds": {
        "wedge_min_s": 900,            # silent, idle, nothing declared: wedged only after this long
        "wedge_p90_factor": 1.5,       # ... or this many times the p90 of the command it is in
        "p90_min_n": 15,               # fewer samples than this and the line says "n<15"
        "busy_cpu_cores": 0.2,         # descendants above this many cores count as working
        "idle_cpu_cores": 0.01,        # at or below this the process tree counts as idle
        "activity_fresh_s": 120,       # a log or ledger that grew this recently counts as progress
        "declared_wait_grace_factor": 1.5,   # a declared wait is overdue at this multiple of its stated length
        "job_max_s": {"build": 3600, "farm": 7200, "floor": 3600, "browser": 3600, "default": 7200},
        "beat_fresh_s": 60,            # a host beat older than this is not evidence the host is fine
        "canary_fresh_s": 900,         # ... and neither is a canary result older than this
        "cpu_sample_ms": 400,          # gap between the two CPU samples of the process tree
    },
    "beat": {"path": None},            # lookout-beat's beat.json; None = the per-OS default
    "lookout": {                       # push verdicts to a Lookout host as a source of their own
        "enabled": False,
        "transport": "http",           # http | local
        "target": None,                # http://host:port, or the lookout command for `local`
        "args": [],                    # `local` only: argv words placed between the command and `ingest`
        "token_file": None,            # the token is read from this file or from token_env, never from argv
        "token_env": None,
        "source_id": "stall-explain",
        "machine": None,
    },
    "doctor": {                        # the Infirmary (`infirmary check`), see docs/catalogue.md
        "platform": None,              # linux | wsl | macos | windows; None = detect
        "proc_root": "/proc",          # where process and memory files are read (a fixture tree in tests)
        "disabled": [],                # check ids to skip
        "hysteresis": True,            # a finding is only confirmed after it has been seen for its `for_s`
        "clear_s": 300,                # a finding that has been absent this long is forgotten (clear duration)
        "for_s": {"duplicate-job-workers": 120, "stale-worker-code": 120, "orphan-job-lanes": 120, "duplicate-watchers": 60,
                  "memory-pressure": 120, "host-unreachable": 60, "host-hung": 60, "host-memory-short": 120,
                  "gate-ahead": 600, "gate-diverged": 120, "slot-busy-unnamed": 120, "abandoned-job-workers": 60,
                  "wsl-vm-near-cap": 120, "wsl-vm-spinning": 120, "wsl-not-running": 120, "leaked-farm-processes": 120,
                  "tmpfs-full": 60, "wsl-interop-missing": 60, "done-without-commit": 120, "meta-pr-order": 60, "slot-held-by-leftover": 120,
                  "farm-process-idle": 300, "pipeline-prepare-skipped": 120,
                  "incident-unreadable": 120, "version-skew": 1800, "version-unknown": 1800},   # by finding kind; a kind not listed confirms at once (the incident rules are dated by their records, see docs/incidents.md)
        "incidents": {                 # incident cards (docs/incidents.md): one append-only row for each confirmed failure that carries a card
            "enabled": True,           # off: no row, no snapshot (the checks still run and report their findings)
            "max_rows": 50,            # rows made per run; more are counted as omitted, never dropped silently
            "tail_kb": 512,            # how much of the ledger's end is read to tell which incidents it already holds
            "ledger_max_kb": 8192,     # the ledger is never rotated or trimmed: at this size it refuses new rows and says so
        },
        "tick_budget_ms": 2000,        # local checks are held to this; a check that overruns is reported, not hidden
        "remote_budget_ms": 20000,     # the ssh probes of other hosts run beside the local checks, under this
        "dry_runs": False,             # also run the owning scripts' dry runs and attach their output (opt-in)
        "dry_run_timeout_s": 20,
        "out": None,                   # where doctor.json goes; None = <state dir>/doctor.json
        "lookout_source_id": "infirmary",
        "disk": {"paths": [], "warn_gb": 40, "crit_gb": 15, "warn_pct": 15, "crit_pct": 7, "inventory_s": 3,
                 "history_samples": 200, "growth_days": 3, "inventory": []},
        "memory": {"avail_warn_pct": 10, "avail_crit_pct": 5, "swap_warn_pct": 70, "swap_crit_pct": 90,
                   "psi_full_warn": 5, "psi_full_crit": 15, "beat_fresh_s": 120,
                   "orphans": {"names": [], "min_age_s": 1800, "min_rss_mb": 200}},
        "scratch": {"groups": [],      # [{"label", "glob" (pattern or list), "min_age_s", "min_mb", "owner_pid_re"}]: scratch a job may leave behind; the adapter names its own
                    "min_age_s": 1800, "min_mb": 200,   # an entry untouched this long, used by no live process; a group counts once its leaked entries hold this much
                    "tmpfs_warn_pct": 90, "tmpfs_crit_pct": 97, "tmpfs_min_mb": 64,   # a RAM-backed volume at this share used; smaller ones are not watched
                    "inventory_s": 3},
        "hosts": [],                   # [{"id": "box", "ssh": "alias", "os": "windows"}]: probed read-only over ssh
        "ssh_timeout_s": 5,
        "ssh_check": {"aliases": [],   # ssh aliases to try besides the hosts (and their guests) above: one non-interactive attempt each, to see whether login waits for a person's additional check
                      "from_hosts": True, "from_ssh_config": False,   # also try the aliases of doctor.hosts and their guests; also try every plain `Host` name in the ssh config
                      "ssh_config": None,   # the ssh config file read when from_ssh_config is on; None = ~/.ssh/config
                      "exclude": [],   # aliases never tried (a git forge's alias, a jump host)
                      "max_aliases": 24},
        "windows": {"avail_warn_mb": 2048, "avail_crit_mb": 1024, "commit_warn_pct": 90, "commit_crit_pct": 95,
                    "old_process_hours": 12, "old_process_warn": 100,
                    "wsl": {"cap_warn_pct": 90, "cap_crit_pct": 97, "cpu_warn_pct": 90},   # the WSL VM (vmmem) against its .wslconfig cap, and its CPU over a sample
                    "leaks": {"min_age_min": 30,   # a process with no live parent younger than this may be a job starting up
                              "servers": ["game-server-standalone.exe"],   # any such process with no live parent is left behind
                              "farm": ["node.exe", "chrome.exe", "chrome-headless-shell.exe", "headless_shell.exe"],   # ... of these only when marked as farm work
                              "farm_markers": ["--headless", "--remote-debugging-port", "ms-playwright", "puppeteer", "chrome-headless-shell"],   # substrings of the command line or executable path
                              "sessions": ["bash.exe", "sshd.exe", "powershell.exe"],   # counted, not listed, once this old and parentless
                              "session_hours": 12, "session_warn": 20, "crit_mb": 4096},
                    "capabilities": {"tools": ["node", "git", "python", "chrome", "spacetime"]},   # looked up by file version (nothing is run); a host's "expect" says what it must offer
                    "platform": {"local": "auto",     # catalogue 23 to 27: also read this machine's own Windows side (from WSL or on Windows); "off" reads only doctor.hosts
                                 "local_id": None,    # the name findings about it carry; None = this host's id
                                 "powershell": None,  # the PowerShell to ask; None = powershell.exe on PATH, else Windows' own under /mnt/c in a WSL guest
                                 "wsl_expected": None,   # on Windows itself: true/false overrides "a WSL 2 distribution is registered"
                                 "timeout_s": 30, "lookback_h": 72,   # how long the local read may take; how far back the System event log is read
                                 "hypervisor_recent_h": 24, "restart_recent_h": 24,   # a launch failure or a Windows Update restart this recent is reported
                                 "update_window_h": 12}},   # updates installed this long before a restart are counted as part of it
        "channels": {"control_sockets": []},   # ssh ControlPath sockets (globs) whose master is checked with `ssh -O check`
        "clobber": {"roots": []},      # worktrees scanned for another platform's native bindings
        "repair": {                    # what `repair` mode may do (docs/repairs.md); the actions themselves are a closed set in code
            "actions": {},             # name -> "plan" (never run unattended, only plan it) or "off"; config can hold an action back, never widen one
            "host_per_hour": 3,        # tier-1 runs per host per hour, all actions together
            "action_per_hour": 2,      # ... per action
            "finding_per_hour": 1,     # ... per finding
            "breaker_open_findings": 4,   # no tier-1 run while this many confirmed findings are open on the host (many snags at once mean something bigger)
            "breaker_failures": 2,     # this many failed or unverified runs of one action in a row trip its breaker ...
            "breaker_cooldown_s": 21600,  # ... for this long (or until `infirmary reset ACTION`)
            "flap_repairs": 3,         # a finding repaired this many times ...
            "flap_window_s": 86400,    # ... within this window is a bug, not a snag: it is no longer repaired
            "plan_ttl_s": 3600,        # a plan not approved within this long is stale
            "timeout_s": 120,          # the owner's repair command is ended after this long
            "output_lines": 40,        # lines of the owner's output kept in the actions log and the plan
        },
    },
    "advice": {                        # `infirmary advise`, the periodic advice-only run an install schedules (docs/advice.md)
        "window_s": 86400,             # explain the alarms the source recorded in this window
        "every_s": 600,                # how often the install runs it (the card stays current for three of these)
        "label": None,                 # names this home on a shared board: the card is stall-explain-<label>
        "card": None,                  # also write the card here (a board's drop directory, or a file)
        "list_max": 20,                # real or unknown alarms listed in advice.txt (all are in advice.json)
        "budget": {                    # what a run may cost, and when it steps aside, saying so (docs/advice.md, "What a run costs")
            "enabled": True,           # off: no limit, no back-off (a person's override for one run: --set advice.budget.enabled=false)
            "wall_s": 30,              # one run's wall time: the explanations stop here and the run says how many alarms it left
            "cpu_s_per_hour": 40,     # CPU-seconds (this process and what it waited for) all the runs of the last hour may use; past it the run does not start
            "load_per_core_max": 0.8,  # the 5-minute load average per core above which the run does not start
            "battery_min_pct": 30,     # on battery below this charge the run does not start ...
            "battery_wall_s": 10,      # ... and on battery above it the wall budget is cut to this
            "power_root": None,        # Linux: where the power supplies are read; None = /sys/class/power_supply (never read for a synthetic /proc)
        },
        "judged": {                    # one append-only row per explained alarm, and one for what the lane did next (docs/judged.md)
            "enabled": True,           # off: no row is appended (the alarms are still explained)
            "settle_s": 1800,          # the window after an alarm in which the lane's own records say what it did next
            "patience_s": 21600,       # a lane that recorded nothing waits this long for a later alarm of its own before it is undecided
            "ledger_max_kb": 16384,    # the ledger is never rotated or trimmed: at this size it refuses new rows and says so (about 1.1 KB an alarm)
        },
    },
    "adapter_config": {},              # free-form settings for the chosen adapter (see its README)
    "files": {                         # the built-in generic source
        "lanes": None,                 # path to lanes.json, see docs/sources.md
    },
}


class ConfigError(Exception):
    pass


def _merge(base, over):
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _read(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ConfigError("cannot read config %s: %s" % (path, exc))
    if path.endswith(".toml"):
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            raise ConfigError("%s is TOML but this Python has no tomllib (3.11+); use a .json config" % path)
        try:
            return tomllib.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ConfigError("%s: %s" % (path, exc))
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfigError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ConfigError("%s must hold a JSON object" % path)
    return data


def parse_value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def apply_sets(cfg, sets):
    """`--set thresholds.wedge_min_s=600` style overrides. Unknown names are an error, not a typo kept."""
    for item in sets or []:
        if "=" not in item:
            raise ConfigError("--set needs name=value, got %r" % item)
        name, _, text = item.partition("=")
        node = cfg
        parts = name.split(".")
        free = parts[0] == "adapter_config" or parts[:2] == ["doctor", "for_s"] or parts[:3] == ["doctor", "repair", "actions"]   # keyed by adapter, finding kind or action
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                raise ConfigError("--set %s: unknown setting %r" % (name, part))
            node = node[part]
        if parts[-1] not in node and not (free and len(parts) > 1):
            raise ConfigError("--set %s: unknown setting %r" % (name, parts[-1]))
        node[parts[-1]] = parse_value(text)
    return cfg


_PS_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PS_MARK = re.compile(r"^[A-Za-z0-9_.:\\/ -]{1,80}$")


def _check_windows(w):
    """These lists are written into the Windows probe, so each word is held to a plain alphabet: configuration can narrow or
    widen what is looked for, never put a command into the script."""
    for key, rx in (("servers", _PS_NAME), ("farm", _PS_NAME), ("sessions", _PS_NAME), ("farm_markers", _PS_MARK)):
        for word in w["leaks"][key]:
            if not isinstance(word, str) or not rx.match(word):
                raise ConfigError("doctor.windows.leaks.%s: %r is not a plain name (letters, digits, . _ - %s)" % (key, word, "and : / \\ space" if rx is _PS_MARK else ""))
    for word in w["capabilities"]["tools"]:
        if not isinstance(word, str) or not _PS_NAME.match(word):
            raise ConfigError("doctor.windows.capabilities.tools: %r is not a plain name" % (word,))
    for key in ("min_age_min", "session_hours", "session_warn", "crit_mb"):
        if not isinstance(w["leaks"][key], int) or isinstance(w["leaks"][key], bool) or w["leaks"][key] < 0:
            raise ConfigError("doctor.windows.leaks.%s must be a whole number of at least 0" % key)
    pc = w["platform"]
    if pc["local"] not in ("auto", "off"):
        raise ConfigError("doctor.windows.platform.local must be \"auto\" or \"off\", got %r" % (pc["local"],))
    for key in ("timeout_s", "lookback_h", "hypervisor_recent_h", "restart_recent_h", "update_window_h"):
        if not isinstance(pc[key], int) or isinstance(pc[key], bool) or pc[key] < 1:
            raise ConfigError("doctor.windows.platform.%s must be a whole number of at least 1" % key)
    if pc["wsl_expected"] not in (None, True, False):
        raise ConfigError("doctor.windows.platform.wsl_expected must be true, false or null")
    for key in ("powershell", "local_id"):
        if pc[key] is not None and not (isinstance(pc[key], str) and pc[key]):
            raise ConfigError("doctor.windows.platform.%s must be a non-empty string or null" % key)


_SSH_ALIAS = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@:+-]{0,127}$")


def _check_ssh(sc):
    """Aliases become arguments of ssh, so each is a plain name (never a leading dash, never a space)."""
    for key in ("aliases", "exclude"):
        if not isinstance(sc[key], list):
            raise ConfigError("doctor.ssh_check.%s must be a list of ssh aliases" % key)
    for alias in sc["aliases"]:
        if not isinstance(alias, str) or not _SSH_ALIAS.match(alias):
            raise ConfigError("doctor.ssh_check.aliases: %r is not a plain ssh alias (letters, digits, . _ @ : + -; no leading dash)" % (alias,))
    if not isinstance(sc["max_aliases"], int) or isinstance(sc["max_aliases"], bool) or not 1 <= sc["max_aliases"] <= 200:
        raise ConfigError("doctor.ssh_check.max_aliases must be a whole number from 1 to 200")


def default_paths():
    out = []
    env = os.environ.get("INFIRMARY_CONFIG")
    if env:
        out.append(env)
    out.append(os.path.join(os.getcwd(), "infirmary.json"))
    home = os.path.expanduser("~")
    out.append(os.path.join(home, ".config", "infirmary", "config.json"))
    return out


def load(path=None, sets=None):
    cfg = copy.deepcopy(DEFAULTS)
    candidates = [path] if path else default_paths()
    loaded = None
    for cand in candidates:
        if cand and os.path.isfile(cand):
            _merge(cfg, _read(cand))
            loaded = cand
            break
    if path and not loaded:
        raise ConfigError("config file not found: %s" % path)
    env_mode = os.environ.get("INFIRMARY_MODE")
    if env_mode:
        cfg["mode"] = env_mode
    apply_sets(cfg, sets)
    if cfg["mode"] == "repair":
        raise ConfigError("live repair is not a configuration setting; the operator turns it on for a home with `infirmary live on` (docs/safety.md)")
    if cfg["mode"] not in ("off", "advise"):
        raise ConfigError("mode must be off or advise, got %r" % cfg["mode"])
    _check_windows(cfg["doctor"]["windows"])
    _check_ssh(cfg["doctor"]["ssh_check"])
    from infirmary.core import budget
    budget.check_config(cfg["advice"]["budget"])
    for name, hold in (cfg["doctor"]["repair"].get("actions") or {}).items():
        if hold not in ("plan", "off"):
            raise ConfigError("doctor.repair.actions.%s must be \"plan\" or \"off\" (configuration can hold an action back, never widen it), got %r" % (name, hold))
    cfg["_loaded_from"] = loaded
    return cfg
