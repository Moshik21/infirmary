"""Catalogue 28: an ssh login that waits for a person (Tailscale SSH's "additional check").

Every configured ssh alias gets one non-interactive, bounded attempt (`BatchMode`, a connect timeout, no control master, a hard timeout, the
no-op command `echo`; the aliases of `doctor.hosts` share the probe `hung-host` already makes, so nothing connects twice) and the answer is
sorted into `ok`, `unreachable`, `auth` (login refused, host key unknown) or `check`: the server printed an additional-check prompt and a login
URL. Only `check` is a finding, because only it is the operator's to clear and silently stalls everything that uses the alias; an alias that does
not answer is `hung-host`'s business and a refused login is shown in the detail. The Infirmary never opens the URL, never completes the check and
never touches ssh config, keys or the network's policy: it reads what ssh printed and says who has to act and what waits.

Aliases come from `doctor.ssh_check.aliases`, from `doctor.hosts` and their guests, and, when `from_ssh_config` is on, from the plain `Host` names of
the operator's ssh config (wildcards and negations are skipped, `Include` files are followed).
"""
import glob
import os
import re
import threading

from infirmary.doctor import finding, registry, sshclass
from infirmary.doctor.checks import hosts as hostmod

ALIAS_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@:+-]{0,127}$")      # never a leading dash: the alias is an argument of ssh
WORKERS = 8
INCLUDE_DEPTH = 3


def config_aliases(path, depth=0):
    """The plain `Host` names in an ssh config file (and the files it includes), in file order. A file that cannot be read gives none."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            rows = fh.read().splitlines()
    except OSError:
        return out
    base = os.path.dirname(path)
    for row in rows:
        row = row.split("#", 1)[0].strip()
        m = re.match(r"^(host|include)(?:\s+|\s*=\s*)(.+)$", row, re.I)      # not HostName, HostKeyAlias
        if not m:
            continue
        if m.group(1).lower() == "host":
            out += [w for w in m.group(2).split() if not re.search(r"[*?!]", w)]
        elif depth < INCLUDE_DEPTH:
            for pat in m.group(2).split():
                pat = os.path.expanduser(pat)
                for inc in sorted(glob.glob(pat if os.path.isabs(pat) else os.path.join(base, pat))):
                    out += config_aliases(inc, depth + 1)
    return out


def collect(ctx):
    """[(alias, where it came from, host entry or None)] without repeats, in a fixed order, capped at `max_aliases`; and what was left out."""
    sc = ctx.d["ssh_check"]
    seen, rows, dropped = set(), [], []
    skip = set(sc["exclude"])

    def add(alias, source, h=None):
        if not isinstance(alias, str) or alias in seen or alias in skip:
            return
        if not ALIAS_RE.match(alias):
            dropped.append(alias[:40])
            return
        seen.add(alias)
        rows.append((alias, source, h))
    if sc["from_hosts"]:
        for h in ctx.d["hosts"]:
            add(h["ssh"], "doctor.hosts %s" % h["id"], h)
        for h in ctx.d["hosts"]:
            if h.get("guest"):
                add(h["guest"]["ssh"], "guest of %s" % h["id"])
    for alias in sc["aliases"]:
        add(alias, "doctor.ssh_check.aliases")
    if sc["from_ssh_config"]:
        for alias in config_aliases(os.path.expanduser(sc["ssh_config"] or "~/.ssh/config")):
            add(alias, "ssh config")
    over = rows[sc["max_aliases"]:]
    return rows[:sc["max_aliases"]], [a for a, _, _ in over] + dropped


def _probe(ctx, alias, h):
    if h is not None:
        return hostmod.ssh_result(ctx, h)       # the host's own probe (a Windows host is asked its PowerShell question): its output carries the same banner
    return hostmod.alias_result(ctx, alias)


def _finding(ctx, alias, source, prompt, hosts_by_alias):
    url = prompt["url"]
    used = hosts_by_alias.get(alias)
    blocks = ("every non-interactive use of the alias `%s` is held behind it: a steer sent to a lane or mate over ssh, a git push or pull over ssh, a job or "
              "canary channel, and the Infirmary's own probes of %s" % (alias, "host " + used if used else "it"))
    why = ("ssh to `%s` stops at an additional-check prompt (\"%s\") instead of logging in. A person has to sign in again at %s before the server lets "
           "anything through, and a non-interactive client cannot do that, so %s. Nothing fails loudly: remote steers and pushes just wait." % (
               alias, prompt["line"], url or "the login URL (none was printed)", blocks))
    return finding.make(
        "ssh-check", 28, "ssh-check-required", alias, "WARNING", ctx.host_id, alias,
        "ssh to %s needs an additional check%s" % (alias, " (the operator signs in at %s)" % url if url else ""), why,
        evidence=[finding.fact("ssh", "BatchMode login to %s printed: %s" % (alias, prompt["line"]), alias),
                  finding.fact("ssh", "login URL printed: %s" % (url or "none"), alias),
                  finding.fact("doctor", "alias taken from %s; the Infirmary did not open the URL and did not complete the check" % source, alias)],
        how=finding.how("the operator (the person who owns the login)",
                        "Open the URL in a browser while signed in as the account that owns the ssh login and finish the sign-in. The Infirmary never opens it or completes "
                        "it. How long a completed check lasts is the `checkPeriod` of the ssh rule in the network's policy file (docs/ssh-check.md); raising it, or "
                        "using `accept` for non-interactive hosts, is the operator's decision, never the Infirmary's."),
        recommendation="Complete the check now (URL above) so steers and pushes to %s go through; until then send nothing there that must arrive, and do not "
                       "count a queued steer as delivered. Consider a longer check period for hosts that lanes depend on." % alias,
        lk_kind="decision")


def check(ctx):
    rows, left_out = collect(ctx)
    if not rows:
        return registry.skip("no ssh alias to try (doctor.ssh_check.aliases, doctor.hosts, or from_ssh_config)")
    results, lock = {}, threading.Semaphore(WORKERS)

    def worker(alias, h):
        with lock:
            results[alias] = _probe(ctx, alias, h)
    threads = [threading.Thread(target=worker, args=(a, h)) for a, _, h in rows]
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join()
    findings, states, local = [], {}, {}
    hosts_by_alias = dict((a, h["id"]) for a, _, h in rows if h is not None)
    for alias, source, _ in rows:
        res = results.get(alias)
        if res is None:
            states[alias] = "failed"
            continue
        st, why, prompt = sshclass.classify(res)
        states[alias] = st
        local[alias] = {"state": st, "url_seen": bool(prompt and prompt["url"])}
        if st == "check":
            findings.append(_finding(ctx, alias, source, prompt, hosts_by_alias))
    counts = {}
    for st in states.values():
        counts[st] = counts.get(st, 0) + 1
    if counts.get("missing") == len(rows):
        return registry.unknown("ssh is not installed here, so no alias could be tried")
    names = [("ok", "ok"), ("check", "waiting for an additional check"), ("auth", "login refused"), ("unreachable", "no answer"), ("failed", "no-op command failed")]
    detail = "%d alias(es) tried: %s" % (len(rows), ", ".join("%d %s" % (counts[k], label) for k, label in names if counts.get(k)) or "none answered")
    if left_out:
        detail += "; not tried (over max_aliases or not a plain alias): %s" % ", ".join(left_out[:6])
    extra = {"local": {"ssh-check": {"aliases": local, "not_tried": len(left_out)}}}
    answered = counts.get("ok", 0) + counts.get("auth", 0) + counts.get("check", 0)
    if not findings and not answered:
        return registry.unknown(detail + " (an additional check could not be ruled out)", **extra)
    return registry.found(findings, detail, **extra)


SPECS = [registry.Spec("ssh-check", 28, "ssh additional-check prompts", check, remote=True)]
