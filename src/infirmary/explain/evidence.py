"""Evidence shared by every source: the empty shape, the activity reader, and the job-record helpers.

An evidence dict is the whole input of the engine (docs/evidence.md); a source's only job is to fill
it from local files and /proc inside the time budget, marking a reader that failed or ran out of time
in `sources` instead of leaving it silently empty.
"""
import glob
import os
import time

from infirmary import EVIDENCE_SCHEMA, EVIDENCE_SCHEMA_VERSION


def empty(subject, as_of):
    return {"schema": EVIDENCE_SCHEMA, "schema_version": EVIDENCE_SCHEMA_VERSION,
            "subject": subject, "as_of": as_of, "lane": {"known": True, "closed": False},
            "trigger": {}, "declarations": [], "agent": {}, "activity": {}, "jobs": [], "history": {},
            "hosts": {}, "pipeline": {}, "sources": []}


class Clock:
    """A wall-clock budget. Readers ask `left()` and skip themselves, saying so, when it is gone."""

    def __init__(self, budget_ms):
        self.end = time.monotonic() + budget_ms / 1000.0
        self.start = time.monotonic()

    def left(self):
        return self.end - time.monotonic()

    def elapsed_ms(self):
        return int((time.monotonic() - self.start) * 1000)


def mark(ev, name, status="ok", ms=0, note=None):
    row = {"name": name, "status": status, "ms": int(ms)}
    if note:
        row["note"] = note
    ev["sources"].append(row)


def activity(patterns, now, window_s=120, max_files=400):
    """Newest mtime across the globs and whether any file changed inside the window."""
    newest, seen = None, 0
    for pat in patterns or []:
        for path in glob.glob(os.path.expanduser(pat), recursive=True):
            try:
                m = os.stat(path).st_mtime
            except OSError:
                continue
            seen += 1
            newest = m if newest is None else max(newest, m)
            if seen >= max_files:
                break
    if newest is None:
        return {"newest_age_s": None, "grew": None, "files": 0}
    age = max(0, int(now - newest))
    return {"newest_age_s": age, "grew": age <= window_s, "files": seen}


def parse_kv(line):
    """`a=1 b=2 cmd=the rest of the line` -> dict; everything after `cmd=` is the command."""
    head, sep, cmd = line.partition(" cmd=")
    out = {}
    for tok in head.split():
        k, eq, v = tok.partition("=")
        if eq:
            out[k] = v
    if sep:
        out["cmd"] = cmd.strip()
    return out


def cmd_key(cmd):
    """A stable key for 'the same command': leading env assignments and directories stripped, program
    plus first argument kept."""
    toks = (cmd or "").split()
    while toks and "=" in toks[0] and not toks[0].startswith("-"):
        toks.pop(0)
    if toks and os.path.basename(toks[0]) == "env":
        toks.pop(0)
        while toks and "=" in toks[0]:
            toks.pop(0)
    toks = [os.path.basename(t) if t.startswith("/") else t for t in toks]
    return " ".join(toks[:2])[:80]


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return int(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))
