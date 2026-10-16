"""What one non-interactive ssh attempt told us: `ok`, `check`, `auth`, `unreachable`, `failed` (or `missing` when there is no ssh).

A login that needs a person is recognised from what the server printed, never by trying to finish it. Tailscale SSH can answer a
connection with an additional-check prompt (`Tailscale SSH requires an additional check.` and a login URL to visit): the person at that
machine has to sign in again before the connection goes on, and `BatchMode` can never do that, so every non-interactive use of the
alias (a steer, a push, a job channel) waits behind it until it is cleared. The text comes from the remote side and is untrusted:
the URL kept is plain printable ASCII, a single token, and is only ever shown to a person, never opened or fetched.
"""
import re

CHECK_RE = re.compile(r"tailscale\s+ssh\s+requires\s+an\s+additional\s+check", re.I)
URL_RE = re.compile(r"https?://[\x21-\x7e]{4,300}", re.I)
AUTH_RE = re.compile(r"permission denied|host key verification failed|remote host identification has changed|no matching (?:host key|key exchange|cipher|mac)"
                     r"|too many authentication failures|no supported authentication methods|authentication failed|not allowed to log in", re.I)
NET_RE = re.compile(r"timed out|connection refused|no route to host|network is unreachable|could not resolve|name or service not known|"
                    r"connection (?:closed|reset)|banner exchange|kex_exchange_identification", re.I)


def recognise(text):
    """The check prompt in `text` as {'url': str or None, 'line': the message}, or None. The URL is the first one printed after the message."""
    m = CHECK_RE.search(text or "")
    if not m:
        return None
    u = URL_RE.search(text, m.end()) or URL_RE.search(text)
    url = u.group(0).rstrip(".,;:)>]'\"") if u else None
    return {"url": url, "line": text[m.start():m.end()].strip()}


def classify(res):
    """(state, detail, prompt) for an `execute.Result` of one ssh probe. `detail` is the first useful line ssh printed; `prompt` is `recognise`'s answer."""
    text = "%s\n%s" % (res.err or "", res.out or "")
    prompt = recognise(text)
    if prompt:
        return "check", "ssh stops at an additional check%s" % ("" if prompt["url"] else " (no login URL was printed)"), prompt
    if res.missing:
        return "missing", "ssh is not installed here", None
    if res.ok:
        return "ok", "answered a no-op command", None
    err = first_line(res.err)
    if AUTH_RE.search(text):
        return "auth", err or "login refused", None
    if res.timed_out or res.rc == 255 or NET_RE.search(text):
        return "unreachable", err or ("no answer in time" if res.timed_out else "ssh exited %s" % res.rc), None
    return "failed", err or "the no-op command exited %s" % res.rc, None


def first_line(text):
    """The first line of ssh's own message, without the warnings it adds (post-quantum notices, banners with `**`)."""
    for row in (text or "").splitlines():
        row = row.strip()
        if row and not row.startswith(("**", "#")) and "post-quantum" not in row and "store now" not in row and "openssh.com" not in row:
            return row[:160]
    return ""
