"""
helpers.py — Shared utilities: logging, subprocess wrapper, privilege check.
"""

import subprocess
import sys
import ctypes
import platform
import datetime
from config import TIMESTAMP_FORMAT

# ── Internal debug flag (set by run_script.py) ────────────────────────────────
_debug_mode: bool = False


def set_debug(enabled: bool) -> None:
    global _debug_mode
    _debug_mode = enabled


def is_debug() -> bool:
    return _debug_mode


# ── Logging ───────────────────────────────────────────────────────────────────

def log_debug(msg: str) -> None:
    """Print only when --debug is active."""
    if _debug_mode:
        ts = datetime.datetime.now().strftime(TIMESTAMP_FORMAT)
        print(f"  [{ts}] {msg}")


def log_user(msg: str) -> None:
    """Always-visible user-facing message."""
    print(msg)


def log_section(title: str) -> None:
    if _debug_mode:
        print(f"\n{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}")


# ── Subprocess helper ─────────────────────────────────────────────────────────

def run_cmd(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """
    Run a shell command safely.
    Returns (returncode, stdout, stderr).
    Never raises — errors returned as non-zero returncode.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        log_debug(f"CMD TIMEOUT: {' '.join(args)}")
        return -1, "", "timeout"
    except FileNotFoundError:
        log_debug(f"CMD NOT FOUND: {args[0]}")
        return -2, "", "not_found"
    except Exception as e:
        log_debug(f"CMD ERROR: {e}")
        return -3, "", str(e)


# ── Platform / privilege ──────────────────────────────────────────────────────

def is_windows() -> bool:
    return platform.system() == "Windows"


def is_admin() -> bool:
    """Return True if the process has administrator / root privileges."""
    try:
        if is_windows():
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            import os
            return os.geteuid() == 0
    except Exception:
        return False


def require_admin_warning() -> None:
    """Warn if not running as admin — some remediations will be skipped."""
    if not is_admin():
        log_user(
            "\n  ⚠  Some optimizations require Administrator privileges.\n"
            "     Right-click the program and choose 'Run as Administrator'\n"
            "     for full remediation capability.\n"
        )
