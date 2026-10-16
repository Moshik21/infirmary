"""What one advise run may cost the machine it watches, and when it steps aside (docs/advice.md, "What a run costs").

Three limits, all in `advice.budget` and all configurable:

  wall time     one run may take at most `wall_s` seconds (`battery_wall_s` on battery); the explanations stop when it is used up, and
                the run says how many alarms it left unexplained
  CPU           the runs of the last hour may use at most `cpu_s_per_hour` CPU-seconds in all (this process and the programs it
                waited for, measured with `os.times()`); past that the run does not start
  back-off      the machine's 5-minute load average per core above `load_per_core_max`, or a battery below `battery_min_pct` percent
                and not on mains power, and the run does not start (a battery above that floor shortens the wall budget)

A run that steps aside says so: `skipped on purpose: load 2.31 per core (limit 0.80)` is the advice row, the card's headline and the
status line, and it is never green and never silent. A reading that could not be taken is shown as not read, never as zero: the run goes
ahead and the budget block says what it could not read. The next scheduled run measures again, so the cadence is back as soon as the load
clears; the ledger (`advice-cost.json` in the state directory) counts the skipped runs so the first full run says it came back.

Read-only: `/proc/loadavg`, `/proc/cpuinfo` and `/sys/class/power_supply` on Linux and WSL, `sysctl` and `pmset` on a Mac (through
`core.execute`), nothing at all on Windows. A fixture's synthetic /proc never reaches for the real machine's power supply.
"""
import json
import os
import re
import tempfile
import time

from infirmary.core import execute

LEDGER = "advice-cost.json"
KEEP_S = 86400
KEEP_N = 300
REAL_PROC = "/proc"
REAL_POWER = "/sys/class/power_supply"


def _read(path, limit=65536):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def _load_linux(root):
    raw = _read(os.path.join(root, "loadavg"))
    try:
        load = float((raw or "").split()[1])
    except (IndexError, ValueError):
        return None, None, "%s/loadavg is not readable" % root
    cpus = sum(1 for row in (_read(os.path.join(root, "cpuinfo"), 1 << 20) or "").splitlines() if row.startswith("processor"))
    if cpus:
        return load, cpus, None
    return load, None, "the number of cores is not readable (%s/cpuinfo)" % root


def _load_mac(run):
    res = run(["sysctl", "-n", "vm.loadavg"], timeout=3)
    nums = re.findall(r"\d+(?:\.\d+)?", res.out) if res.ok else []
    ncpu = run(["sysctl", "-n", "hw.ncpu"], timeout=3)
    if len(nums) < 2:
        return None, None, "sysctl vm.loadavg gave no load average"
    if not (ncpu.ok and ncpu.out.strip().isdigit() and int(ncpu.out.strip()) > 0):
        return float(nums[1]), None, "sysctl hw.ncpu gave no core count"
    return float(nums[1]), int(ncpu.out.strip()), None


def load(cfg, osk, run=None):
    """-> {"per_core", "load5", "cores", "why"}: the 5-minute load average divided by the cores; `per_core` is None, with the reason in
    `why`, when either could not be read."""
    if osk == "macos":
        l5, cores, why = _load_mac(run or execute.run)
    elif osk in ("linux", "wsl"):
        l5, cores, why = _load_linux(cfg["doctor"]["proc_root"])
    else:
        l5, cores, why = None, None, "this OS has no load average to read"
    return {"per_core": round(l5 / cores, 2) if l5 is not None and cores else None, "load5": l5, "cores": cores, "why": why}


def _power_linux(root):
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None, None, "%s is not readable" % root
    bats, mains_on = [], False
    for n in names:
        kind = (_read(os.path.join(root, n, "type"), 64) or "").strip()
        if kind == "Battery":
            cap = (_read(os.path.join(root, n, "capacity"), 16) or "").strip()
            bats.append({"status": (_read(os.path.join(root, n, "status"), 32) or "").strip(), "pct": int(cap) if cap.isdigit() else None})
        elif kind == "Mains" and (_read(os.path.join(root, n, "online"), 8) or "").strip() == "1":
            mains_on = True
    if not bats:
        return False, None, None            # a machine with no battery is on mains power: a fact, not a guess
    if mains_on or not any(b["status"] == "Discharging" for b in bats):
        return False, None, None
    pcts = [b["pct"] for b in bats if b["status"] == "Discharging" and b["pct"] is not None]
    return True, min(pcts) if pcts else None, None if pcts else "the battery's charge is not readable"


def _power_mac(run):
    res = run(["pmset", "-g", "batt"], timeout=3)
    if not res.ok or "drawing from" not in res.out:
        return None, None, "pmset -g batt gave no answer"
    on = "Battery Power" in res.out.splitlines()[0]
    m = re.search(r"(\d+)%", res.out)
    return on, (int(m.group(1)) if on and m else None), None if (m or not on) else "pmset gave no charge level"


def power(cfg, osk, run=None):
    """-> {"on_battery": True/False/None, "pct", "why"}: None, with the reason, when the machine's power state could not be read."""
    if osk == "macos":
        on, pct, why = _power_mac(run or execute.run)
    elif osk in ("linux", "wsl"):
        root = cfg["advice"]["budget"].get("power_root")
        if root is None and cfg["doctor"]["proc_root"] == REAL_PROC:
            root = REAL_POWER
        if root is None:
            on, pct, why = None, None, "no power supply directory is declared for a synthetic /proc"
        else:
            on, pct, why = _power_linux(os.path.expanduser(root))
    else:
        on, pct, why = None, None, "this OS has no power state to read"
    return {"on_battery": on, "pct": pct, "why": why}


def _ledger_path(sd):
    return os.path.join(sd, LEDGER)


def read_ledger(sd):
    try:
        with open(_ledger_path(sd), encoding="utf-8") as fh:
            doc = json.load(fh)
        runs = [r for r in doc.get("runs", []) if isinstance(r, dict) and isinstance(r.get("at"), (int, float))]
    except (OSError, ValueError, AttributeError):
        runs = []
    return runs


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def plan(cfg, now, sd, osk, run=None):
    """Decide before the run starts: {"decision": "full" | "shortened" | "skipped", "reason", "wall_s", "limits", "readings", ...}.
    `reason` is the plain sentence the advice row and the status line carry; it is None for a full run."""
    b = cfg["advice"]["budget"]
    out = {"enabled": bool(b["enabled"]), "decision": "full", "reason": None, "wall_s": float(b["wall_s"]),
           "limits": {"wall_s": b["wall_s"], "cpu_s_per_hour": b["cpu_s_per_hour"], "load_per_core_max": b["load_per_core_max"],
                      "battery_min_pct": b["battery_min_pct"], "battery_wall_s": b["battery_wall_s"]},
           "load": None, "power": None, "cpu_s_last_hour": None, "backed_off_runs": 0, "backed_off_since": None, "notes": []}
    runs = read_ledger(sd)
    out["cpu_s_last_hour"] = round(sum(r.get("cpu_s") or 0 for r in runs if now - r["at"] < 3600), 2)
    streak = 0
    for r in reversed(runs):
        if r.get("decision") != "skipped":
            break
        streak += 1
    out["backed_off_runs"] = streak
    if streak:
        out["backed_off_since"] = _iso(runs[-streak]["at"])
    if not b["enabled"]:
        out["notes"].append("the budget is switched off (advice.budget.enabled)")
        return out
    out["load"], out["power"] = load(cfg, osk, run), power(cfg, osk, run)
    ld, pw = out["load"], out["power"]
    if ld["why"]:
        out["notes"].append("load not read: %s" % ld["why"])
    if pw["why"]:
        out["notes"].append("power not read: %s" % pw["why"])
    if ld["per_core"] is not None and ld["per_core"] > b["load_per_core_max"]:
        out["decision"], out["reason"] = "skipped", "load %.2f per core (limit %.2f)" % (ld["per_core"], b["load_per_core_max"])
    elif pw["on_battery"] and pw["pct"] is not None and pw["pct"] < b["battery_min_pct"]:
        out["decision"], out["reason"] = "skipped", "on battery at %d%% (floor %d%%)" % (pw["pct"], b["battery_min_pct"])
    elif out["cpu_s_last_hour"] >= b["cpu_s_per_hour"]:
        out["decision"], out["reason"] = "skipped", "cpu budget used: %.1f of %s cpu-seconds in the last hour" % (out["cpu_s_last_hour"], b["cpu_s_per_hour"])
    elif pw["on_battery"]:
        out["decision"] = "shortened"
        out["wall_s"] = min(out["wall_s"], float(b["battery_wall_s"]))
        out["reason"] = "on battery%s: wall budget cut to %s s" % (" at %d%%" % pw["pct"] if pw["pct"] is not None else "", b["battery_wall_s"])
    return out


def cpu_now():
    """CPU-seconds this process and the programs it waited for have used so far."""
    t = os.times()
    return t[0] + t[1] + t[2] + t[3]


def record(sd, now, entry):
    """Append this run to the ledger (last day, at most KEEP_N runs): {at, decision, cpu_s, wall_s}. A write that fails is the caller's problem."""
    runs = [r for r in read_ledger(sd) if now - r["at"] < KEEP_S][-(KEEP_N - 1):]
    runs.append(dict(entry, at=int(now)))
    os.makedirs(sd, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=sd, prefix=".cost.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"runs": runs}, fh)
        os.replace(tmp, _ledger_path(sd))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_config(b):
    """The limits are numbers that make sense; anything else is a configuration error, not a silent default."""
    from infirmary.core.config import ConfigError
    for key in ("wall_s", "cpu_s_per_hour", "battery_wall_s"):
        if not isinstance(b.get(key), (int, float)) or isinstance(b.get(key), bool) or b[key] <= 0:
            raise ConfigError("advice.budget.%s must be a number above 0" % key)
    if not isinstance(b.get("load_per_core_max"), (int, float)) or isinstance(b.get("load_per_core_max"), bool) or b["load_per_core_max"] <= 0:
        raise ConfigError("advice.budget.load_per_core_max must be a number above 0")
    if not isinstance(b.get("battery_min_pct"), int) or isinstance(b.get("battery_min_pct"), bool) or not 0 <= b["battery_min_pct"] <= 100:
        raise ConfigError("advice.budget.battery_min_pct must be a whole number from 0 to 100")
    if not isinstance(b.get("enabled"), bool):
        raise ConfigError("advice.budget.enabled must be true or false")
    if b.get("power_root") is not None and not (isinstance(b["power_root"], str) and b["power_root"]):
        raise ConfigError("advice.budget.power_root must be a path or null")
