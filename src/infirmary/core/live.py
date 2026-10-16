"""The live-repair toggle: one switch, per home, off unless an operator turned it on.

`<state dir>/live-repair.json` records whether the Infirmary may run its tier-1 repairs on this home, who set it and when. With no
file, an unreadable file, an `enabled: false` file or a file written for another host, adapter or home, the Infirmary is
advice-only: findings and plans, nothing acted on. Only `infirmary live on` writes `enabled: true`. No configuration key,
environment variable, default, installer or adapter can turn it on, and nothing in the Infirmary calls `turn` except that command.

Turning it on changes nothing about the gates. The kill switch, the circuit breakers, flap detection and the rate limits apply
exactly as before, and the kill switch wins over the toggle.
"""
import json
import os
import time

from infirmary.core import hostid, state

SCHEMA = "infirmary.live-repair"
SCHEMA_VERSION = "1.0"
ADVICE_ONLY = "advice-only"
LIVE_REPAIR = "live-repair"


def path(cfg):
    return os.path.join(state.state_dir(cfg), "live-repair.json")


def scope(cfg):
    """What the toggle is for: this host, this adapter and, when the adapter has one, this home. A record written for another
    scope does not count here, so a copied or shared state directory never switches repairs on somewhere else."""
    home = (cfg.get("adapter_config") or {}).get("home")
    return {"host": hostid.identity(cfg)["id"], "adapter": cfg.get("adapter"),
            "home": os.path.realpath(os.path.expanduser(home)) if isinstance(home, str) and home else None}


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def read(cfg):
    """{on, recorded, by, at, why}: `on` is True only for a readable record that says enabled for exactly this scope."""
    out = {"on": False, "recorded": None, "by": None, "at": None, "file": path(cfg), "why": "never turned on (the default)"}
    try:
        with open(out["file"], encoding="utf-8") as fh:
            rec = json.load(fh)
    except FileNotFoundError:
        return out
    except (OSError, ValueError) as exc:
        out["why"] = "the record %s cannot be read (%s); treated as off" % (out["file"], type(exc).__name__)
        return out
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA or not isinstance(rec.get("enabled"), bool):
        out["why"] = "the record %s is not a live repair record (schema %s); treated as off" % (out["file"], SCHEMA)
        return out
    out.update(recorded=rec["enabled"], by=rec.get("by"), at=rec.get("at"))
    if not rec["enabled"]:
        out["why"] = "turned off%s" % (" by %s at %s" % (rec.get("by"), rec.get("at")) if rec.get("by") else "")
    elif rec.get("scope") != scope(cfg):
        out["why"] = "turned on for another host, adapter or home (%s); here it is off until `infirmary live on` is run here" % json.dumps(rec.get("scope"), sort_keys=True)
    else:
        out.update(on=True, why="turned on by %s at %s" % (rec.get("by"), rec.get("at")))
    return out


def turn(cfg, on, by):
    """Record the operator's choice. Raises OSError when it cannot be written (the caller says so; nothing is half-set)."""
    now = time.time()
    rec = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "enabled": bool(on), "by": by, "at": _iso(now), "epoch": int(now), "scope": scope(cfg)}
    p = path(cfg)
    state.state_dir(cfg, create=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return rec


def apply(cfg):
    """Resolve the mode this run really has. `advise` becomes `repair` only when the toggle is on here; `cfg["_live"]` keeps the
    record for status and doctor.json. Nothing else in the configuration can produce `repair`."""
    cfg["mode"] = cfg.setdefault("_config_mode", cfg["mode"])
    cfg["_live"] = read(cfg)
    if cfg["mode"] == "advise" and cfg["_live"]["on"]:
        cfg["mode"] = "repair"
    return cfg


def label(cfg):
    """The name for the dashboard: advice-only or live-repair (off when the configuration says the tool is off)."""
    return {"repair": LIVE_REPAIR, "advise": ADVICE_ONLY}.get(cfg.get("mode"), cfg.get("mode"))


def summary(cfg):
    """The small block doctor.json and `status --json` carry: the toggle as recorded, not the effective mode."""
    live = cfg.get("_live") or read(cfg)
    return {"enabled": live["on"], "by": live["by"], "at": live["at"], "why": live["why"]}
