"""Catalogue 19: WSL interop not registered. After a WSL restart the kernel's binfmt_misc table can come back without its
`WSLInterop` entry, and every Windows executable (`powershell.exe`, `cmd.exe`, `clip.exe`) then fails inside the guest with
"Exec format error" while the rest of the machine looks fine. Detection reads one directory of the kernel's table; it needs no
privilege and registers nothing (re-registering takes root, and is a person's call)."""
import os

from infirmary.doctor import finding, registry

NAMES = ("WSLInterop", "WSLInterop-late")


def _first_line(path):
    try:
        with open(path, "rb") as fh:
            return fh.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None


def check(ctx):
    if ctx.osk != "wsl":
        return registry.skip("not a WSL guest (%s has no Windows interop to register)" % ctx.osk)
    table = os.path.join(ctx.proc_root, "sys", "fs", "binfmt_misc")
    if not (os.path.isfile(os.path.join(table, "register")) or os.path.isfile(os.path.join(table, "status"))):
        return registry.unknown("binfmt_misc is not mounted at %s, so the interop entry cannot be read" % table)
    present = [n for n in NAMES if os.path.isfile(os.path.join(table, n))]
    others = sorted(n for n in os.listdir(table) if n not in ("register", "status") and n not in NAMES)[:6] if os.path.isdir(table) else []
    ev = [finding.fact("binfmt_misc", "entries: %s" % (", ".join(present + others) or "none"), table)]
    state = None
    if present:
        state = _first_line(os.path.join(table, present[0]))
        ev.append(finding.fact("binfmt_misc", "%s is %s" % (present[0], state or "unreadable"), os.path.join(table, present[0])))
        if state == "enabled":
            return registry.ok("%s registered and enabled" % present[0], local={"wsl-interop": {"registered": present[0], "state": state}})
        if state is None:
            return registry.unknown("%s exists but cannot be read" % present[0])
    why = ("the kernel's binfmt_misc table has no WSLInterop entry: Windows programs cannot be started from this guest (powershell.exe, cmd.exe, "
           "tools that shell out to them fail with \"Exec format error\")" if not present else
           "the WSLInterop entry is registered but %s: Windows programs cannot be started from this guest" % (state or "not enabled"))
    f = finding.make(
        "wsl-interop", 19, "wsl-interop-missing", ctx.host_id, "WARNING", ctx.host_id, ctx.host_id,
        "Windows interop is %s in this WSL guest" % ("missing" if not present else "switched off"), why, evidence=ev,
        how=finding.how("WSL", "Registering the entry needs root inside the guest (mount binfmt_misc and register WSLInterop), or a restart of the WSL VM from "
                        "Windows after the lanes on it are recorded; the Infirmary changes nothing."),
        recommendation="Check /etc/wsl.conf for `[interop] enabled=false`; otherwise a person with root re-registers the entry or restarts the VM once no lane is mid-job.",
        lk_kind="machine", retires_with=[], target={"table": table, "present": present})
    return registry.found([f], why, local={"wsl-interop": {"registered": present[0] if present else None, "state": state}})


SPECS = [registry.Spec("wsl-interop", 19, "WSL interop (Windows programs)", check)]
