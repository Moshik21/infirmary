"""The verdict vocabulary and the one JSON shape every finding, verdict and check result uses.

Shaped on the guard contract of a supervising watcher (name, status, verdict, subject, why, evidence,
recommendation) so one can consume it directly: OK/WARNING/CRITICAL say the condition explains the
worker's silence, and UNKNOWN means the evidence did not decide. See docs/verdicts.md.
"""
import time

from infirmary import VERDICT_SCHEMA, VERDICT_SCHEMA_VERSION, VERSION

# name -> (status, exit code, benign, meaning)
VERDICTS = {
    "queued": ("OK", 0, True, "waiting for a slot or a pipeline turn"),
    "running-long": ("OK", 0, True, "a long job is making progress"),
    "running-remote": ("OK", 0, True, "the job runs on another host that is healthy"),
    "thinking": ("OK", 0, True, "the agent itself is working"),
    "declared-wait": ("OK", 0, True, "the worker declared a long wait and it is not overdue"),
    "torn-down": ("OK", 0, True, "the lane is gone; there is nobody to wake"),
    "waiting-on-supervisor": ("WARNING", 10, True, "the worker is done or asked a question; the supervisor's move"),
    "waiting-on-human": ("WARNING", 20, False, "a prompt or dialog needs a person"),
    "wedged": ("CRITICAL", 30, False, "alive, idle, no progress anywhere"),
    "dead": ("CRITICAL", 40, False, "nothing is there"),
    "unknown": ("UNKNOWN", 50, False, "the evidence does not decide; never a guess"),
}
EXIT_OFF = 0
EXIT_USAGE = 2
EXIT_INTERNAL = 3


def exit_code(name):
    return VERDICTS[name][1]


def is_benign(name):
    """Benign or supervisor-owed: nothing a worker-side watcher should escalate."""
    return VERDICTS[name][2]


def make(verdict, subject, why, evidence, recommendation="", qualifiers=None, took_ms=0, budget_ms=1000, checked_at=None):
    status = VERDICTS[verdict][0]
    return {
        "schema": VERDICT_SCHEMA, "schema_version": VERDICT_SCHEMA_VERSION,
        "name": "stall-explain", "tool_version": VERSION,
        "checked_at": checked_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": status, "verdict": verdict, "qualifiers": list(qualifiers or []),
        "subject": subject, "why": why, "evidence": evidence,
        "recommendation": recommendation,   # text for a person, never a command
        "exit_code": VERDICTS[verdict][1],
        "budget_ms": budget_ms, "took_ms": took_ms,
    }


def line(v):
    """`verdict · subject · why`, plus qualifiers: the one line a person reads."""
    extra = "".join(" [%s]" % q for q in v.get("qualifiers", []))
    return "%s%s · %s · %s" % (v["verdict"], extra, v["subject"], v["why"])
