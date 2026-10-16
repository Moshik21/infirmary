"""Run another program, bounded, without a shell. The only way the Infirmary starts anything.

argv is always a list (never a string for a shell), stdin is closed unless input is given, output is
capped, and a hard timeout ends the child. Only a process this module started is ever signalled (by
the timeout, and only that child); nothing here looks up a process by name or pid.
"""
import os
import subprocess
import time

MAX_OUT = 200000


class Result(object):
    def __init__(self, rc, out, err, timed_out, took_ms, missing=False):
        self.rc, self.out, self.err, self.timed_out, self.took_ms, self.missing = rc, out, err, timed_out, took_ms, missing

    @property
    def ok(self):
        return self.rc == 0 and not self.timed_out and not self.missing


def run(argv, timeout=5.0, env=None, cwd=None, input_text=None):
    """Return a Result; never raises. A program that is not installed has missing=True and rc None. `env` adds to the environment; None removes."""
    full = dict(os.environ)
    for k, v in (env or {}).items():     # a value of None takes the variable out of the child's environment
        if v is None:
            full.pop(k, None)
        else:
            full[k] = v
    t0 = time.time()
    try:
        res = subprocess.run(list(argv), stdin=subprocess.DEVNULL if input_text is None else None, input=None if input_text is None else input_text.encode("utf-8"),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(0.2, timeout), env=full, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"")[:MAX_OUT].decode("utf-8", "replace")
        return Result(None, out, (exc.stderr or b"")[:MAX_OUT].decode("utf-8", "replace"), True, int((time.time() - t0) * 1000))
    except (OSError, ValueError):
        return Result(None, "", "", False, int((time.time() - t0) * 1000), missing=True)
    return Result(res.returncode, res.stdout[:MAX_OUT].decode("utf-8", "replace"), res.stderr[:MAX_OUT].decode("utf-8", "replace"),
                  False, int((time.time() - t0) * 1000))


def git(args, cwd=None, git_dir=None, timeout=5.0):
    """A read-only git call: no optional locks, no pager, no prompts. Callers pass only read verbs."""
    argv = ["git"]
    if git_dir:
        argv += ["--git-dir", git_dir]
    elif cwd:
        argv += ["-C", cwd]
    return run(argv + list(args), timeout=timeout, env={"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C"})
