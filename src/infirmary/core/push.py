"""Push verdicts to a Lookout host as a source of their own (E4).

Every lane becomes an item whose `state_detail` carries the verdict line; only wedged, dead,
waiting-on-human and unknown raise an `attention` card, so the board never fills with explained silence.
The token comes from a file or an environment variable, never argv. A host that is down is normal:
one failed attempt returns an error string and nothing else is affected.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

from infirmary import VERSION
from infirmary.core import findings, hostid

STATE_OF = {"waiting-on-supervisor": "waiting", "waiting-on-human": "blocked", "wedged": "blocked", "dead": "failed",
            "unknown": "unknown", "torn-down": "done", "declared-wait": "paused"}
ATTENTION = {"wedged": "critical", "dead": "critical", "waiting-on-human": "action", "unknown": "info"}
LIMIT_ITEMS, LIMIT_ATTENTION = 60, 30


def build_feed(verdicts, cfg, seq, boot_id):
    lk = cfg["lookout"]
    host = hostid.identity(cfg)
    machine = lk.get("machine") or host["id"]
    items, attention = [], []
    for v in verdicts[:LIMIT_ITEMS]:
        lane = v["subject"]
        items.append({"id": lane, "title": lane, "machine": machine, "state": STATE_OF.get(v["verdict"], "running"),
                      "doing": findings.line(v)[:1900], "state_detail": v["verdict"],
                      "last_event": {"state": v["verdict"], "text": v["why"][:1900], "at": v["checked_at"]}})
        sev = ATTENTION.get(v["verdict"])
        if sev and len(attention) < LIMIT_ATTENTION:
            attention.append({"id": "stall:%s" % lane, "kind": "service", "severity": sev, "title": "%s: %s" % (lane, v["verdict"]),
                              "detail": v["why"][:1900], "item": lane, "machine": machine, "how": v.get("recommendation") or None})
    return {"schema": "lookout.feed", "schema_version": "1.0",
            "source": {"id": lk.get("source_id") or "stall-explain", "label": "Stall explainer", "machine": machine, "kind": "standalone"},
            "producer": {"name": "stall-explain", "version": VERSION, "boot_id": boot_id, "seq": seq, "mode": "live"},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "interval_s": 30,
            "items": items, "attention": attention}


def _next_seq(state_dir, name="push.seq"):
    """seq increases within a boot_id; the boot id is ours (start time), kept with the counter."""
    path = os.path.join(state_dir, name)
    boot, seq = "boot-%d" % int(time.time()), 0
    try:
        with open(path) as fh:
            old_boot, _, old_seq = fh.read().strip().partition(" ")
            if old_boot and old_seq.isdigit():
                boot, seq = old_boot, int(old_seq)
    except OSError:
        pass
    seq += 1
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w") as fh:
            fh.write("%s %d\n" % (boot, seq))
    except OSError:
        pass
    return boot, seq


def _token(lk):
    if lk.get("token_file"):
        try:
            with open(os.path.expanduser(lk["token_file"]), encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return None
    if lk.get("token_env"):
        return os.environ.get(lk["token_env"])
    return None


def push(verdicts, cfg, state_dir):
    """Return (ok, text). Raises nothing."""
    return deliver(lambda seq, boot: build_feed(verdicts, cfg, seq, boot), cfg, state_dir, cfg["lookout"].get("source_id") or "stall-explain", "push.seq")


def deliver(build, cfg, state_dir, source_id, seq_name):
    """Send the feed `build(seq, boot_id)` returns to the configured Lookout host, as `source_id`. Return (ok, text)."""
    lk = cfg["lookout"]
    if not lk.get("enabled") or not lk.get("target"):
        return False, "lookout push is not enabled"
    boot, seq = _next_seq(state_dir, seq_name)
    body = json.dumps(build(seq, boot)).encode("utf-8")
    try:
        if lk["transport"] == "http":
            req = urllib.request.Request(lk["target"].rstrip("/") + "/api/ingest", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            token = _token(lk)
            if token:
                req.add_header("Authorization", "Bearer " + token)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, resp.read().decode("utf-8", "replace").strip()
        if lk["transport"] == "local":
            res = subprocess.run([lk["target"]] + list(lk.get("args") or []) + ["ingest", "--source", source_id],
                                 input=body, capture_output=True, timeout=10)
            return res.returncode == 0, res.stdout.decode("utf-8", "replace").strip()
        return False, "unknown transport %r (http or local)" % lk["transport"]
    except urllib.error.HTTPError as exc:
        return False, "HTTP %d: %s" % (exc.code, exc.read().decode("utf-8", "replace").strip())
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return False, str(exc)
