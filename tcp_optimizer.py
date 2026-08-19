"""
tcp_optimizer.py — Safe, one-shot TCP/IP stack tuning.

Only runs netsh commands that are well understood and reversible by Windows.
Never loops. Never flushes or resets aggressively.
"""

from helpers import run_cmd, log_debug, is_admin


def flush_dns() -> bool:
    """
    ipconfig /flushdns — clears stale/poisoned DNS cache.
    Very safe. No infrastructure impact.
    """
    log_debug("Action: ipconfig /flushdns")
    rc, stdout, stderr = run_cmd(["ipconfig", "/flushdns"])
    success = rc == 0 and "successfully" in stdout.lower()
    log_debug(f"  DNS flush → {'OK' if success else 'FAILED'} (rc={rc})")
    return success


def optimize_tcp() -> bool:
    """
    Reset TCP auto-tuning to 'normal' and enable RSS.
    Requires admin. Safe — does not disconnect sessions.
    """
    if not is_admin():
        log_debug("TCP optimization skipped — not running as admin.")
        return False

    success = True

    log_debug("Action: netsh int tcp set global autotuninglevel=normal")
    rc, _, _ = run_cmd(
        ["netsh", "int", "tcp", "set", "global", "autotuninglevel=normal"]
    )
    if rc != 0:
        log_debug("  autotuninglevel reset failed (may already be normal).")
        success = False

    log_debug("Action: netsh int tcp set global rss=enabled")
    rc, _, _ = run_cmd(
        ["netsh", "int", "tcp", "set", "global", "rss=enabled"]
    )
    if rc != 0:
        log_debug("  RSS enable failed.")
        success = False

    log_debug(f"  TCP optimization → {'OK' if success else 'PARTIAL/FAILED'}")
    return success


def reset_winsock() -> bool:
    """
    netsh winsock reset — resets winsock catalog.
    Medium-risk: safe but requires reboot to fully take effect.
    Only called when stack-level failure is confirmed.
    Requires admin.
    """
    if not is_admin():
        log_debug("Winsock reset skipped — not running as admin.")
        return False

    log_debug("Action: netsh winsock reset  [MEDIUM-RISK]")
    rc, stdout, _ = run_cmd(["netsh", "winsock", "reset"])
    success = rc == 0
    log_debug(f"  Winsock reset → {'OK' if success else 'FAILED'} (rc={rc})")
    return success
