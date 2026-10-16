"""Check registry and the adapter hook loader.

A check is `Spec(id, catalogue, title, fn, remote)`; `fn(ctx)` returns `ok(...)`, `unknown(...)`,
`skip(...)` or a list of findings via `found(...)`. A check never raises into the runner and never
says `ok` without something it saw.
"""
import importlib.util
import os
import re
import sys

from infirmary.explain import sources


class Spec(object):
    """`family="incident"` marks a rule of the incident family (docs/incidents.md): its confirmed findings carry a card and become incident
    rows, and when it cannot read its input (`unknown`, or it breaks) it still produces an unknown row instead of silence."""

    def __init__(self, id, catalogue, title, fn, remote=False, family=None):
        self.id, self.catalogue, self.title, self.fn, self.remote, self.family = id, catalogue, title, fn, remote, family


def ok(detail, **extra):
    return ("ok", detail, [], extra)


def unknown(detail, **extra):
    return ("unknown", detail, [], extra)


def skip(detail, **extra):
    return ("skipped", detail, [], extra)


def found(findings, detail="", **extra):
    return ("finding" if findings else "ok", detail, findings, extra)


def core_specs():
    from infirmary.doctor.checks import disk, memory, hosts, clobber, windows, interop, scratch, platform, sshcheck
    return disk.SPECS + memory.SPECS + hosts.SPECS + windows.SPECS + clobber.SPECS + interop.SPECS + scratch.SPECS + platform.SPECS + sshcheck.SPECS


class AdapterError(Exception):
    """The configured adapter cannot be used: not found in any searched directory, or found and failed to load. The message names it and
    the directories searched; `short` is the same fact in a few words (the incidents line says it)."""

    def __init__(self, message, short):
        Exception.__init__(self, message)
        self.short = short


def adapter_dirs(cfg):
    return [os.path.abspath(os.path.expanduser(p)) for p in cfg.get("adapter_paths", [])] + [os.path.join(sources.REPO, "adapters")]


def load_adapter(cfg):
    """The adapter's `doctor.py` hooks, or None for the built-in `files` adapter and for an adapter that only explains alarms (a `source.py`
    and no `doctor.py`). It offers `hooks(cfg) -> {"checks": [Spec], "disk_inventory": fn, "clobber_roots": fn}`.
    A named adapter that is in none of the searched directories, or that fails to load, raises `AdapterError`: never a silent skip."""
    name = cfg.get("adapter") or "files"
    if name == "files":
        return None
    dirs = adapter_dirs(cfg)
    for base in dirs:
        path = os.path.join(base, name, "doctor.py")
        if os.path.isfile(path):
            try:
                spec = importlib.util.spec_from_file_location("infirmary_doctor_" + re.sub(r"\W", "_", name), path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
                return mod.hooks(cfg)
            except Exception as exc:
                raise AdapterError("adapter %s could not be loaded from %s: %s: %s" % (name, path, type(exc).__name__, exc), "adapter %s could not be loaded" % name)
        if os.path.isfile(os.path.join(base, name, "source.py")):
            return None
    raise AdapterError("adapter %s not found (looked in %s)" % (name, ", ".join(dirs)), "adapter %s not found" % name)
