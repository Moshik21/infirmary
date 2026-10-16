"""`<state>/actions.jsonl`: one JSON line per thing the tool did or refused to do.

In advise mode the only entries are `explain` records (what was asked, what was said); the same
file will carry repairs and refusals when the Infirmary arrives. Bounded: rotated at 1 MiB, one
generation kept. A failure to write is never allowed to change a verdict.
"""
import json
import os
import time

MAX_BYTES = 1 << 20


def record(state_dir, tool, kind, mode, **fields):
    row = {"ts": int(time.time()), "tool": tool, "kind": kind, "tier": "T0", "mode": mode}
    row.update(fields)
    try:
        os.makedirs(state_dir, exist_ok=True)
        path = os.path.join(state_dir, "actions.jsonl")
        try:
            if os.path.getsize(path) > MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False
