#!/usr/bin/env python3
"""Build a tiny live fleet in a temp directory so `stall-explain LANE` has something real to read.

    python3 examples/demo-fleet/make_demo.py DIR
    python3 examples/demo-fleet/make_demo.py --stop DIR
    stall-explain --set files.lanes=DIR/lanes.json --list
    stall-explain --set files.lanes=DIR/lanes.json lane-finished

Five lanes with real files and two real child processes (the busy one runs at nice 19; both stop by themselves after 60 seconds):
a finished lane, a lane in a declared wait, a lane whose process tree is burning CPU, a lane whose
process sits idle (add --idle-s 3600 to say it has been silent an hour: wedged), and a lane whose
process is gone (dead). The children's pids are written to DIR/pids.json; `--stop DIR` ends exactly those
pids (each only if it is still the process that was recorded, never by name or pattern) and removes the file.
Anything that builds a demo should call `--stop DIR` when it is done, also on failure. Standard library only; works on Linux, WSL and macOS (the CPU
and process-identity reads need /proc, so on macOS that lane reports less).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

LIFE = 60
BUSY = "import os, time\nos.nice(19)\nt=time.time()+%d\nwhile time.time()<t:\n    sum(range(200000))\n" % LIFE


def stop(out):
    """End the children recorded in OUT/pids.json, only those pids and only while they are still the recorded processes."""
    path = os.path.join(out, "pids.json")
    try:
        recorded = json.load(open(path))
    except (OSError, ValueError):
        return 0
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
        from infirmary.core import proc
    except ImportError:
        proc = None
    for rec in recorded:
        if proc is not None and rec.get("starttime") is not None and proc.same(rec) is False:
            continue    # gone, or the pid belongs to something else now
        try:
            os.kill(rec["pid"], signal.SIGTERM)
        except OSError:
            pass
    os.remove(path)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], epilog="\n".join(__doc__.split("\n")[2:]),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", help="the directory to build the demo fleet in (made if missing)")
    ap.add_argument("--stop", metavar="DIR", help="end the demo's recorded children in DIR and remove its pid file")
    args = ap.parse_args()
    if bool(args.dir) == bool(args.stop):
        ap.error("give a directory to build the demo in, or --stop DIR")
    if args.stop:
        return stop(os.path.abspath(args.stop))
    out = os.path.abspath(args.dir)
    os.makedirs(out, exist_ok=True)
    now = int(time.time())

    def write(name, lines):
        with open(os.path.join(out, name), "w") as fh:
            fh.write("\n".join(lines) + "\n")

    write("finished.log", ["working [at=%d]: started" % (now - 3000), "done [at=%d]: change committed, ready for review" % (now - 1800)])
    write("waiting.log", ["working [at=%d]: started" % (now - 2400), "paused [at=%d]: long benchmark running, about 1-2h" % (now - 1500)])
    write("quiet.log", ["working [at=%d]: started" % (now - 7200)])
    child = subprocess.Popen([sys.executable, "-c", BUSY], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    idle = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(%d)" % LIFE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.2)
    ident, idle_ident = {"pid": child.pid}, {"pid": idle.pid}
    with open(os.path.join(out, "pids.json"), "w") as fh:
        json.dump([ident, idle_ident], fh)
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
        from infirmary.core import proc
        ident = proc.identity(child.pid) or ident
        idle_ident = proc.identity(idle.pid) or idle_ident
        with open(os.path.join(out, "pids.json"), "w") as fh:
            json.dump([ident, idle_ident], fh)
    except ImportError:
        pass
    doc = {"lanes": {
        "lane-finished": {"declarations": "finished.log"},
        "lane-waiting": {"declarations": "waiting.log"},
        "lane-busy": {"declarations": "quiet.log", "agent": ident},
        "lane-quiet": {"declarations": "quiet.log", "agent": idle_ident},
        "lane-gone": {"declarations": "quiet.log", "agent": {"pid": 2 ** 22 - 7}}}}
    with open(os.path.join(out, "lanes.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    print("demo fleet in %s (child processes %d and %d stop by themselves in %d seconds)" % (out, child.pid, idle.pid, LIFE))
    print("try:  stall-explain --set files.lanes=%s/lanes.json lane-busy -v" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
