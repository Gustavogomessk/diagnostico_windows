import os
import sys

from .config import is_frozen


def is_admin() -> bool:
    if sys.platform != "win32":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Reabre o programa com elevacao (UAC). O usuario confirma no prompt do Windows."""
    if sys.platform != "win32":
        return False
    import ctypes
    args = " ".join(f'"{a}"' for a in sys.argv[1:])
    if is_frozen():
        exe, params = sys.executable, args
    else:
        exe, params = sys.executable, "-m techdiag " + args
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32
