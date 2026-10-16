"""`stall-explain --replay FILE`: run the engine over recorded alarms and score it against hand labels.

A replay file is JSON Lines. The first line is a header naming the classes and which verdicts count as
agreeing with each; every other line is one alarm with its recorded evidence snapshot and (optionally)
the hand-assigned class. The engine is a pure function of the snapshot, so a replay is exact and
repeatable, and it is the accuracy measurement a verdict set must pass before anyone relies on it.
"""
import json
import time

from infirmary import REPLAY_SCHEMA
from infirmary.core import findings
from infirmary.doctor import judged
from infirmary.explain import engine

GATE_BENIGN_RATE = 0.80


class ReplayError(Exception):
    pass


def load(path):
    header, alarms = None, []
    try:
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError as exc:
                    raise ReplayError("%s:%d: not JSON: %s" % (path, n, exc))
                if row.get("schema") != REPLAY_SCHEMA:
                    raise ReplayError("%s:%d: not a replay line (schema %s)" % (path, n, REPLAY_SCHEMA))
                if row.get("kind") == "header":
                    header = row
                else:
                    alarms.append(row)
    except OSError as exc:
        raise ReplayError("cannot read %s: %s" % (path, exc))
    if header is None:
        raise ReplayError("%s has no header line" % path)
    return header, alarms


def run(header, alarms, cfg):
    out = []
    for a in alarms:
        t0 = time.monotonic()
        v = engine.explain(a["evidence"], cfg, took_ms=0)
        v["took_ms"] = int((time.monotonic() - t0) * 1000)
        out.append({"id": a["id"], "at": a.get("at"), "class": a.get("class"), "scored": a.get("scored", True), "verdict": v})
    return out


def score(header, results):
    classes = header.get("classes", {})
    table, misses = {}, []
    real_total = real_flagged = rest_total = rest_benign = rest_exact = 0
    for r in results:
        if not r["scored"] or r["class"] is None:
            continue
        spec = classes.get(r["class"], {})
        verdict = r["verdict"]["verdict"]
        row = table.setdefault(r["class"], {"n": 0, "benign": 0, "exact": 0, "flagged": 0, "verdicts": {}})
        row["n"] += 1
        row["verdicts"][verdict] = row["verdicts"].get(verdict, 0) + 1
        benign = findings.is_benign(verdict)
        row["benign"] += 1 if benign else 0
        row["flagged"] += 0 if benign else 1
        exact = verdict in (spec.get("expect") or []) if spec.get("expect") != "flagged" else not benign
        row["exact"] += 1 if exact else 0
        if spec.get("expect") == "flagged":
            real_total += 1
            real_flagged += 0 if benign else 1
            if benign:
                misses.append((r, "real stall called %s" % verdict))
        else:
            rest_total += 1
            rest_benign += 1 if benign else 0
            rest_exact += 1 if exact else 0
            if not benign:
                misses.append((r, "expected benign or supervisor-owed, got %s" % verdict))
    rate = (rest_benign / float(rest_total)) if rest_total else 1.0
    return {"by_class": table, "misses": misses, "judged": _judged(header, results),
            "real": {"total": real_total, "flagged": real_flagged},
            "rest": {"total": rest_total, "benign_or_owed": rest_benign, "exact": rest_exact, "rate": rate},
            "gate": {"every_real_flagged": real_flagged == real_total, "benign_rate_at_least": GATE_BENIGN_RATE,
                     "passed": real_flagged == real_total and rate >= GATE_BENIGN_RATE}}


def _judged(header, results):
    """The verdicts judged against what each hand class says the lane did next (a class's `outcome`), by the same table the advise run uses
    (docs/judged.md). None when no class names an outcome. Offline: nothing is written to a ledger, and an alarm whose class names no
    outcome is counted as not labelled, never as agreeing."""
    classes = header.get("classes", {})
    if not any((spec or {}).get("outcome") for spec in classes.values()):
        return None
    out = {"basis": "hand-labelled outcomes (offline)", "alarms": 0, "explained": 0, "real": 0, "unknown": 0, "agreed": 0, "wrong_benign": 0,
           "wrong_real": 0, "undecided": 0, "not_labelled": 0, "wrong_benign_alarms": []}
    for r in results:
        outcome = (classes.get(r["class"]) or {}).get("outcome") if r["class"] else None
        if not outcome:
            out["not_labelled"] += 1
            continue
        verdict = r["verdict"]["verdict"]
        out["alarms"] += 1
        out[judged.KIND[judged.CLAIM[verdict]]] += 1
        j, _ = judged.judge(verdict, outcome)
        out[judged.key(j)] += 1
        if j == judged.WRONG_BENIGN:
            out["wrong_benign_alarms"].append(r["id"])
    return out


def render(header, results, sc):
    lines = ["replay: %s · %d alarms (%d scored)" % (header.get("title", "untitled"), len(results), sum(1 for r in results if r["scored"] and r["class"]))]
    cols = ["class", "n", "benign/owed", "exact", "flagged", "verdicts"]
    rows = [cols]
    for name, row in sorted(sc["by_class"].items()):
        rows.append([name, str(row["n"]), str(row["benign"]), str(row["exact"]), str(row["flagged"]),
                     ", ".join("%s %d" % (k, v) for k, v in sorted(row["verdicts"].items()))])
    widths = [max(len(r[i]) for r in rows) for i in range(len(cols))]
    for i, r in enumerate(rows):
        lines.append("  " + "  ".join(c.ljust(widths[j]) for j, c in enumerate(r)).rstrip())
        if i == 0:
            lines.append("  " + "  ".join("-" * w for w in widths))
    real, rest, gate = sc["real"], sc["rest"], sc["gate"]
    lines.append("real stalls flagged: %d of %d" % (real["flagged"], real["total"]))
    lines.append("everything else benign or supervisor-owed: %d of %d (%.1f%%; gate %.0f%%); exact class: %d of %d" % (
        rest["benign_or_owed"], rest["total"], 100 * rest["rate"], 100 * gate["benign_rate_at_least"], rest["exact"], rest["total"]))
    lines.append("gate: %s" % ("PASS" if gate["passed"] else "FAIL"))
    if sc["misses"]:
        lines.append("misses:")
        for r, why in sc["misses"]:
            lines.append("  %s · class %s · %s · %s" % (r["id"], r["class"], why, engine._clip(r["verdict"]["why"], 80)))
    jd = sc.get("judged")
    if jd:
        lines.append("judged against the hand-labelled outcomes (offline): %d alarms · explained %d · real %d · unknown %d · agreed %d · wrong-benign %d · wrong-real %d · undecided %d%s" % (
            jd["alarms"], jd["explained"], jd["real"], jd["unknown"], jd["agreed"], jd["wrong_benign"], jd["wrong_real"], jd["undecided"],
            " (%d not labelled)" % jd["not_labelled"] if jd["not_labelled"] else ""))
    extra = [r for r in results if not (r["scored"] and r["class"])]
    if extra:
        lines.append("unscored alarms (no hand label, or outside the scored set): %d" % len(extra))
    return "\n".join(lines)
