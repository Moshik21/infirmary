"""The Infirmary's state as one status card (`lookout.card` 1.0): the same seam as feed.py, a smaller output.

A card is a small JSON file a dashboard can read from a drop directory. The Infirmary writes it only when asked
(`check --card PATH`, or `card`); it imports no dashboard and the dashboard imports nothing of the Infirmary. The `mode` field
carries the repair mode as the operator set it: `advice-only` or `live-repair`.
"""
import json
import os
import tempfile

from infirmary.core import live, state
from infirmary.doctor import unknowns

TOOL = "infirmary"
STATUS = {"OK": "ok", "WARNING": "warn", "CRITICAL": "bad", "UNKNOWN": "unknown"}
DEFAULT_STALE_S = 900


def _count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _clip(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def build(doc, cfg, stale_after_s=DEFAULT_STALE_S):
    """The card for a doctor.json document (what `check` returns), in the repair mode the home is in now."""
    live_f = [f for f in doc["findings"] if not f["pending"]]
    crit = sum(1 for f in live_f if f["status"] == "CRITICAL")
    warn = sum(1 for f in live_f if f["status"] == "WARNING")
    plans = len((doc.get("repairs") or {}).get("plans") or [])
    status = STATUS.get(doc["status"], "unknown")
    unk = doc["unknowns"] if "unknowns" in doc else unknowns.of(doc)    # a document from before 1.7 has none stored
    parts = [_count(len(live_f), "finding")] if live_f else ["no findings" if unk else "all clear"]
    if crit:
        parts[0] += " (%d critical)" % crit
    if plans:
        parts.append("%s waiting for you" % _count(plans, "plan"))
    if unk:      # never "all clear" beside a check that could not decide: the count, and the first reason, are on the tile
        parts.append("%d unknown%s: %s" % (len(unk), "" if len(unk) == 1 else ", first", unknowns.line(unk[0], 90)))
    if state.is_off(cfg):
        status, parts = "unknown", ["off (kill switch); this is the last check"]
    return {"schema": "lookout.card", "schema_version": "1.0", "tool": TOOL, "title": "Infirmary", "status": status,
            "headline": _clip(", ".join(parts), 160),
            "numbers": [{"label": "findings", "value": len(live_f)}, {"label": "critical", "value": crit},
                        {"label": "warnings", "value": warn}, {"label": "unknown", "value": len(unk)}, {"label": "plans waiting", "value": plans}],
            "updated_at": doc["checked_at"], "mode": live.label(cfg), "stale_after_s": int(stale_after_s)}


def target(path):
    """-> the file a --card PATH names: PATH itself, or <PATH>/infirmary.json when PATH is a directory."""
    path = os.path.expanduser(path)
    return os.path.join(path, TOOL + ".json") if os.path.isdir(path) or path.endswith(("/", os.sep)) else path


def write(path, card):
    """Write the card atomically (temp file, then rename) so a reader never sees half of it. -> the file written."""
    dest = target(path)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)), prefix="." + TOOL + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(card, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest
