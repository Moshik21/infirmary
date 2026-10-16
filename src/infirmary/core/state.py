"""The tool's own state directory and its kill switch. Nothing else on the machine is ever written."""
import os
import sys


def default_dir():
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "infirmary")
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "infirmary")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "infirmary")


def state_dir(cfg, create=False):
    path = cfg.get("state_dir") or os.environ.get("INFIRMARY_STATE") or default_dir()
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def switch_file(cfg):
    return os.path.join(state_dir(cfg), "off")


def is_off(cfg):
    """Off by `mode`, by the `off` file in the state directory, or by INFIRMARY=off."""
    if cfg.get("mode") == "off" or os.environ.get("INFIRMARY", "").lower() == "off":
        return True
    return os.path.exists(switch_file(cfg))


def set_off(cfg, off):
    path = switch_file(cfg)
    if off:
        state_dir(cfg, create=True)
        with open(path, "w") as fh:
            fh.write("off\n")
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
