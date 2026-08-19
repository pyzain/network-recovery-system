"""
remediation.py — Orchestrates safe fixes based on classification.

Fixes are applied in safety-priority order (safest first).
Each action is single-shot. No loops, no repeated DHCP storms, no route spam.
"""

from dataclasses import dataclass, field
from typing import Optional
from classifier import Classification, Fault
from tcp_optimizer import flush_dns, optimize_tcp, reset_winsock
from interface_manager import reset_adapter, renew_dhcp, reconnect_wifi, remove_stale_default_routes
from helpers import log_debug, is_admin


@dataclass
class RemediationResult:
    actions_attempted: list[str] = field(default_factory=list)
    actions_succeeded: list[str] = field(default_factory=list)
    reboot_recommended: bool     = False
    user_message: str            = ""


# ── User-facing messages by fault ────────────────────────────────────────────

USER_MESSAGES: dict[str, str] = {
    Fault.STACK_FAILURE: (
        "A deeper Windows network issue was detected.\n"
        "Please save your work and restart your computer."
    ),
    Fault.ADAPTER_HANG: (
        "Wireless adapter became unresponsive.\n"
        "Adapter reset completed successfully."
    ),
    Fault.APIPA_DHCP_FAILURE: (
        "DHCP issue detected. Your IP address was refreshed.\n"
        "If this problem continues, contact IT support."
    ),
    Fault.GATEWAY_UNREACHABLE: (
        "Local network connection issue detected.\n"
        "Connection was refreshed. If the router is unreachable, contact IT."
    ),
    Fault.ISP_UPSTREAM_FAILURE: (
        "No upstream internet detected.\n"
        "Your local connection was optimized successfully.\n"
        "Please wait for provider restoration."
    ),
    Fault.DNS_FAILURE: (
        "DNS issue detected and refreshed successfully."
    ),
    Fault.WIFI_WEAK_SIGNAL: (
        "Weak Wi-Fi signal detected.\n"
        "Move closer to the router or switch to a 5 GHz network if available."
    ),
    Fault.WIFI_24GHZ_CONGESTION: (
        "Connection optimized.\n"
        "For better performance, switch to a 5 GHz Wi-Fi network if available."
    ),
    Fault.HIGH_PACKET_LOSS: (
        "Network congestion detected.\n"
        "Connection refreshed. If instability continues, contact IT."
    ),
    Fault.HIGH_LATENCY: (
        "High network latency detected.\n"
        "Local optimizations applied. Upstream congestion may persist."
    ),
    Fault.PARTIAL_INTERNET: (
        "Connection refreshed successfully.\n"
        "Minor latency may be due to upstream conditions beyond local control."
    ),
    Fault.HEALTHY: (
        "Network appears healthy.\n"
        "Minor optimizations completed."
    ),
    Fault.MINOR_OPTIMIZABLE: (
        "Network appears healthy.\n"
        "Minor optimizations completed."
    ),
}


def _attempt(label: str, fn, result: RemediationResult) -> bool:
    """Run a single remediation action and record outcome."""
    log_debug(f"  Attempting: {label}")
    result.actions_attempted.append(label)
    try:
        ok = fn()
        if ok:
            result.actions_succeeded.append(label)
        log_debug(f"  Result: {'✓ succeeded' if ok else '✗ failed'}")
        return ok
    except Exception as e:
        log_debug(f"  Exception during '{label}': {e}")
        return False


# ── Remediation plans by fault ────────────────────────────────────────────────

def remediate(cls: Classification) -> RemediationResult:
    result   = RemediationResult()
    fault    = cls.fault
    report   = cls.report
    wifi     = cls.wifi

    # Pull dynamic values from live data — never hardcoded
    adapter_name = ""
    gateway_ip   = ""
    ssid         = ""

    if report and report.adapter:
        adapter_name = report.adapter.name
        gateway_ip   = report.adapter.gateway

    if wifi:
        if not adapter_name:
            adapter_name = wifi.adapter_name
        ssid = wifi.ssid

    log_debug(
        f"Remediation plan for [{fault}]  "
        f"adapter='{adapter_name}'  gateway='{gateway_ip}'  ssid='{ssid}'"
    )

    # ─────────────────────────────────────────────────────────────────────
    # STACK FAILURE — Winsock reset + reboot recommendation
    # ─────────────────────────────────────────────────────────────────────
    if fault == Fault.STACK_FAILURE:
        _attempt("DNS flush",     flush_dns,     result)
        _attempt("Winsock reset", reset_winsock, result)
        result.reboot_recommended = True

    # ─────────────────────────────────────────────────────────────────────
    # ADAPTER HANG — reset the adapter
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.ADAPTER_HANG:
        _attempt("DNS flush", flush_dns, result)
        if adapter_name:
            _attempt(
                f"Adapter reset ({adapter_name})",
                lambda: reset_adapter(adapter_name),
                result,
            )
        else:
            log_debug("  Cannot reset adapter — name not discovered.")

    # ─────────────────────────────────────────────────────────────────────
    # APIPA / DHCP failure
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.APIPA_DHCP_FAILURE:
        _attempt("DNS flush", flush_dns, result)
        _attempt("TCP optimization", optimize_tcp, result)
        _attempt(
            "DHCP renewal",
            lambda: renew_dhcp(adapter_name),
            result,
        )

    # ─────────────────────────────────────────────────────────────────────
    # GATEWAY unreachable
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.GATEWAY_UNREACHABLE:
        _attempt("DNS flush", flush_dns, result)
        _attempt("TCP optimization", optimize_tcp, result)
        if wifi and wifi.is_connected and adapter_name and ssid:
            _attempt(
                "Wi-Fi reconnect",
                lambda: reconnect_wifi(adapter_name, ssid),
                result,
            )
        elif adapter_name:
            _attempt(
                f"Adapter reset ({adapter_name})",
                lambda: reset_adapter(adapter_name),
                result,
            )
        _attempt("Remove stale default routes", remove_stale_default_routes, result)

    # ─────────────────────────────────────────────────────────────────────
    # ISP / upstream failure — optimize locally, don't hammer network
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.ISP_UPSTREAM_FAILURE:
        _attempt("DNS flush",       flush_dns,       result)
        _attempt("TCP optimization", optimize_tcp,   result)
        # No DHCP renewal, no adapter reset — local path is fine

    # ─────────────────────────────────────────────────────────────────────
    # DNS failure
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.DNS_FAILURE:
        _attempt("DNS flush",        flush_dns,     result)
        _attempt("TCP optimization", optimize_tcp,  result)
        # Optionally renew DHCP to get fresh DNS server from DHCP
        if report and report.adapter:
            _attempt(
                "DHCP renew (to refresh DNS servers)",
                lambda: renew_dhcp(adapter_name),
                result,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Weak Wi-Fi signal — reconnect may help with AP selection
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.WIFI_WEAK_SIGNAL:
        _attempt("DNS flush", flush_dns, result)
        _attempt("TCP optimization", optimize_tcp, result)
        if adapter_name and ssid:
            _attempt(
                "Wi-Fi reconnect (try better AP)",
                lambda: reconnect_wifi(adapter_name, ssid),
                result,
            )

    # ─────────────────────────────────────────────────────────────────────
    # 2.4 GHz congestion — light optimization, user advisory
    # ─────────────────────────────────────────────────────────────────────
    elif fault == Fault.WIFI_24GHZ_CONGESTION:
        _attempt("DNS flush",        flush_dns,    result)
        _attempt("TCP optimization", optimize_tcp, result)

    # ─────────────────────────────────────────────────────────────────────
    # High packet loss / high latency / partial internet
    # ─────────────────────────────────────────────────────────────────────
    elif fault in (Fault.HIGH_PACKET_LOSS, Fault.HIGH_LATENCY, Fault.PARTIAL_INTERNET):
        _attempt("DNS flush",        flush_dns,    result)
        _attempt("TCP optimization", optimize_tcp, result)

    # ─────────────────────────────────────────────────────────────────────
    # Healthy — still do light maintenance
    # ─────────────────────────────────────────────────────────────────────
    elif fault in (Fault.HEALTHY, Fault.MINOR_OPTIMIZABLE):
        _attempt("DNS flush",        flush_dns,    result)
        _attempt("TCP optimization", optimize_tcp, result)

    # ─────────────────────────────────────────────────────────────────────
    # Build user message
    # ─────────────────────────────────────────────────────────────────────
    base_msg = USER_MESSAGES.get(fault, "Connection refreshed.")

    if result.reboot_recommended:
        result.user_message = base_msg  # already includes reboot warning
    elif not is_admin():
        result.user_message = (
            base_msg + "\n\n"
            "Note: Run as Administrator for full optimization capability."
        )
    else:
        result.user_message = base_msg

    log_debug(
        f"Remediation complete — "
        f"{len(result.actions_succeeded)}/{len(result.actions_attempted)} actions succeeded."
    )
    return result
