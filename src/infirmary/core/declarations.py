"""The declaration log grammar: what a worker says about itself, one line at a time.

    state [key=slug] [at=1790000000]: free text

`state` is one of working, paused, done, blocked, needs-decision, failed, resolved. Bracketed tags may
come in any order before the first colon. `resolved [key=x]` closes the open decision with that key
(or, with no key, the newest open one). A paused line may state how long the wait is ("~1-2h",
"until 2026-10-08T21:30Z"); that is parsed into `wait_hint_s` / `until` so the free text is not needed
to judge an overdue wait.
"""
import calendar
import re
import time

STATES = ("working", "paused", "done", "blocked", "needs-decision", "failed", "resolved")
_TAG = re.compile(r"\[(\w+)=([^\]]*)\]")
_DUR = re.compile(r"~?\s*(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?\s*(h|hr|hrs|hours?|min|mins|minutes?|m)\b", re.I)
_UNTIL = re.compile(r"until\s+(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})Z?", re.I)


def wait_hint(text):
    """Longest stated duration in seconds ("~1-2h" is 7200), or None."""
    best = None
    for m in _DUR.finditer(text or ""):
        hi = float(m.group(2) or m.group(1))
        unit = m.group(3).lower()
        secs = hi * (3600 if unit.startswith("h") else 60)
        best = secs if best is None else max(best, secs)
    return int(best) if best else None


def until_epoch(text):
    m = _UNTIL.search(text or "")
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    return calendar.timegm((y, mo, d, h, mi, 0))


def parse_line(line):
    line = line.rstrip("\n")
    if ":" not in line:
        return None
    head, _, text = line.partition(":")
    words = head.split()
    if not words or words[0] not in STATES:
        return None
    tags = dict(_TAG.findall(head))
    at = int(tags["at"]) if tags.get("at", "").isdigit() else None
    return {"at": at, "state": words[0], "key": tags.get("key") or None, "text": text.strip(),
            "wait_hint_s": wait_hint(text) if words[0] == "paused" else None,
            "until": until_epoch(text) if words[0] == "paused" else None}


def read(path, as_of=None):
    """Declarations in file order. Lines without an `at` take the previous line's time; with
    `as_of`, nothing newer than that moment is returned (this is what makes a replay honest)."""
    out, last = [], None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                row = parse_line(raw)
                if row is None:
                    continue
                if row["at"] is None:
                    row["at"] = last
                else:
                    last = row["at"]
                if as_of is not None and row["at"] is not None and row["at"] > as_of:
                    continue
                out.append(row)
    except OSError:
        return None
    return out


def effective(rows):
    """What the log says the worker is doing now: (kind, row), kind one of working, paused, done,
    failed, blocked, needs-decision or resumed. `resolved` closes the decision it names; once none is
    open the worker owes work again (`resumed`), whatever the last event line said. (None, None)
    when there is nothing."""
    open_rows, last = [], None
    for row in rows:
        st = row["state"]
        last = row
        if st in ("blocked", "needs-decision"):
            open_rows.append(row)
        elif st == "resolved":
            if row["key"]:
                open_rows = [r for r in open_rows if r["key"] != row["key"]]
            elif open_rows:
                open_rows.pop()
        else:
            open_rows = []  # a fresh working/paused/done/failed line supersedes older open calls
    if last is None:
        return None, None
    if last["state"] == "resolved":
        return ("resumed", last) if not open_rows else (open_rows[-1]["state"], open_rows[-1])
    return last["state"], last


def now_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))
