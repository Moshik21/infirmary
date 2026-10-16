"""What the fake `ssh` and the fake `powershell` share: a PowerShell script reaches them only the way the product sends it (ONE line on
standard input that decodes to the script), and a script holding a verb that could change anything on the host is refused. The test
of the Infirmary's read-only promise for every Windows script is that these shims accept it. Not imported by the product."""
import base64
import re
import sys

FORBIDDEN = re.compile(r"\b(Stop|Remove|Set|Restart|Start|Clear|Kill|Add|Disable|Enable|Move|Copy|Rename|Out|Export|Register|Unregister|Suspend|Resume|Install|Uninstall|"
                       r"Update|Invoke|Mount|Dismount|Reset|Write)-(?!Sleep\b)\w+|\btaskkill\b|\bwsl(\.exe)?\b|\.Kill\(|\.Terminate\(|\bsc\.exe\b|\bnet\s+(stop|start)\b|"
                       r"\bshutdown\b|\bbcdedit\b|\breg(\.exe)?\s+(add|delete|import)\b|\bschtasks\b|\bdism\b|\bformat\b\s|[^-]>\s*[\$\"'A-Za-z]", re.I)


def refuse(why):
    sys.stderr.write("%s: refused: %s\n" % (sys.argv[0].rsplit("/", 1)[-1], why))
    sys.exit(1)


def decode(stdin):
    """-> the script, or refuse: standard input must be one decode-and-run line, the script must start by silencing errors and hold no
    verb that changes something."""
    lines = [l for l in stdin.split("\n") if l.strip()]
    m = re.match(r"^iex \(\[Text\.Encoding\]::UTF8\.GetString\(\[Convert\]::FromBase64String\('([A-Za-z0-9+/=]+)'\)\)\)$", lines[0]) if lines else None
    if len(lines) != 1 or not m:
        refuse("standard input is not one decode-and-run line (a multi-line block is dropped by powershell -Command -)")
    script = base64.b64decode(m.group(1)).decode("utf-8")
    bad = FORBIDDEN.search(re.sub(r"(?m)^\s*#.*$", "", script))
    if bad:
        refuse("the script holds %r, which could change something on the host" % bad.group(0))
    if not script.lstrip().startswith("$ErrorActionPreference='SilentlyContinue'"):
        refuse("the script does not start by silencing errors")
    left = re.search(r"__[A-Z_]+__", script)
    if left:
        refuse("the script still holds a placeholder: %s" % left.group(0))
    return script
