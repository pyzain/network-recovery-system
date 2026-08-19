"""
interface_manager.py — Adapter-level remediations.

All adapter names are passed in dynamically — nothing hardcoded.
DHCP renewal is conditional; adapter resets are single-shot.
"""

import time
from helpers import run_cmd, log_debug, is_admin
from config import ADAPTER_RESET_WAIT_S, WIFI_RECONNECT_WAIT_S, MAX_DHCP_ATTEMPTS


# ── Adapter enable / disable ──────────────────────────────────────────────────

def reset_adapter(adapter_name: str) -> bool:
    """
    Disable then re-enable a named adapter.
    Single-shot — never loops.
    Requires admin.
    """
    if not is_admin():
        log_debug(f"Adapter reset skipped for '{adapter_name}' — not admin.")
        return False

    log_debug(f"Action: disable adapter '{adapter_name}'")
    rc_off, _, _ = run_cmd(
        ["netsh", "interface", "set", "interface",
         f"name={adapter_name}", "admin=disabled"]
    )
    if rc_off != 0:
        log_debug(f"  Disable failed (rc={rc_off}) — adapter may need a different name format.")
        return False

    log_debug(f"  Waiting {ADAPTER_RESET_WAIT_S}s before re-enable…")
    time.sleep(ADAPTER_RESET_WAIT_S)

    log_debug(f"Action: enable adapter '{adapter_name}'")
    rc_on, _, _ = run_cmd(
        ["netsh", "interface", "set", "interface",
         f"name={adapter_name}", "admin=enabled"]
    )
    success = rc_on == 0
    log_debug(f"  Re-enable → {'OK' if success else 'FAILED'} (rc={rc_on})")
    return success


# ── DHCP renewal ──────────────────────────────────────────────────────────────

def renew_dhcp(adapter_name: str = "") -> bool:
    """
    ipconfig /release + /renew — conditional, single attempt.
    Only call this when APIPA or corrupted lease is confirmed.
    Requires admin for best results (works partially without).
    """
    target = f'"{adapter_name}"' if adapter_name else ""

    log_debug(f"Action: ipconfig /release {target}  [CONDITIONAL]")
    run_cmd(["ipconfig", "/release"] + ([adapter_name] if adapter_name else []),
            timeout=20)

    time.sleep(2)

    log_debug(f"Action: ipconfig /renew {target}")
    rc, stdout, _ = run_cmd(
        ["ipconfig", "/renew"] + ([adapter_name] if adapter_name else []),
        timeout=30,
    )
    success = rc == 0
    log_debug(f"  DHCP renew → {'OK' if success else 'FAILED'} (rc={rc})")
    return success


# ── Wi-Fi reconnect ───────────────────────────────────────────────────────────

def reconnect_wifi(adapter_name: str, ssid: str) -> bool:
    """
    Disconnect and reconnect to the current SSID.
    Uses dynamically discovered adapter name and SSID — nothing hardcoded.
    Requires admin or standard user depending on profile.
    """
    if not ssid:
        log_debug("Wi-Fi reconnect skipped — no SSID available.")
        return False

    log_debug(f"Action: netsh wlan disconnect interface='{adapter_name}'")
    run_cmd(["netsh", "wlan", "disconnect", f"interface={adapter_name}"],
            timeout=10)

    log_debug(f"  Waiting {WIFI_RECONNECT_WAIT_S}s before reconnect…")
    time.sleep(WIFI_RECONNECT_WAIT_S)

    log_debug(f"Action: netsh wlan connect name='{ssid}' interface='{adapter_name}'")
    rc, stdout, _ = run_cmd(
        ["netsh", "wlan", "connect",
         f"name={ssid}", f"interface={adapter_name}"],
        timeout=20,
    )
    success = rc == 0
    log_debug(f"  Wi-Fi reconnect → {'OK' if success else 'FAILED'} (rc={rc})")
    return success


# ── Route table cleanup ───────────────────────────────────────────────────────

def remove_stale_default_routes() -> bool:
    """
    Detect and remove duplicate default routes (0.0.0.0) conservatively.
    Dangerous-class action — only call when routing is confirmed broken.
    Requires admin.
    """
    if not is_admin():
        log_debug("Route cleanup skipped — not admin.")
        return False

    log_debug("Action: route print 0.0.0.0 (stale route check)")
    rc, stdout, _ = run_cmd(["route", "print", "0.0.0.0"], timeout=10)
    if rc != 0:
        return False

    # Count default route entries
    defaults = [
        line for line in stdout.splitlines()
        if line.strip().startswith("0.0.0.0")
    ]
    log_debug(f"  Found {len(defaults)} default route(s).")

    if len(defaults) <= 1:
        log_debug("  No duplicate default routes — cleanup skipped.")
        return True

    # Remove all, then the OS will re-establish via DHCP or existing adapter
    log_debug(f"  Removing {len(defaults) - 1} duplicate default route(s).")
    for entry in defaults[1:]:  # keep the first (lowest metric), remove extras
        parts = entry.split()
        if len(parts) >= 3:
            gateway = parts[2]
            log_debug(f"    route delete 0.0.0.0 mask 0.0.0.0 {gateway}")
            run_cmd(["route", "delete", "0.0.0.0", "mask", "0.0.0.0", gateway],
                    timeout=5)

    return True
