"""
classifier.py — Maps diagnostic + Wi-Fi data to a single named fault scenario.

The classifier is the "brain" — it reads facts and returns a verdict.
It does NOT execute any fixes. That is remediation.py's job.
"""

from dataclasses import dataclass
from typing import Optional
from diagnostics import DiagnosticReport
from wifi_analysis import WifiInfo
from helpers import log_debug


# Fault scenario catalogue

class Fault:
    # OS / Stack level
    STACK_FAILURE         = "windows_network_stack_failure"
    ADAPTER_HANG          = "adapter_hang_or_driver_fault"

    # Local network level
    APIPA_DHCP_FAILURE    = "apipa_dhcp_failure"
    GATEWAY_UNREACHABLE   = "gateway_unreachable"
    WIFI_WEAK_SIGNAL      = "poor_wifi_signal"
    WIFI_SLOW_LINK        = "wifi_slow_link_speed"
    WIFI_24GHZ_CONGESTION = "wifi_24ghz_congestion"

    # Upstream / ISP level
    ISP_UPSTREAM_FAILURE  = "isp_or_upstream_failure"
    PARTIAL_INTERNET      = "partial_internet_connectivity"

    # Application layer
    DNS_FAILURE           = "dns_resolution_failure"
    HIGH_PACKET_LOSS      = "high_packet_loss_congestion"
    HIGH_LATENCY          = "high_latency"

    # Healthy
    HEALTHY               = "network_healthy"
    MINOR_OPTIMIZABLE     = "network_healthy_minor_issues"


@dataclass
class Classification:
    fault: str
    severity: str       # "critical" / "degraded" / "advisory" / "healthy"
    description: str    # human-readable for debug log
    wifi: Optional[WifiInfo] = None
    report: Optional[DiagnosticReport] = None


# Classifier logic

def classify(report: DiagnosticReport, wifi: Optional[WifiInfo]) -> Classification:
    """
    Evaluate report + wifi and return the single most actionable Classification.
    Priority order matters: most severe first.
    """

    def make(fault: str, severity: str, desc: str) -> Classification:
        log_debug(f"Classification → [{severity.upper()}] {fault}: {desc}")
        return Classification(
            fault=fault, severity=severity, description=desc,
            wifi=wifi, report=report
        )

    # 1. Windows TCP/IP stack failure (most severe)
    if not report.loopback_ok:
        return make(
            Fault.STACK_FAILURE, "critical",
            "Loopback ping to 127.0.0.1 failed — TCP/IP stack may be corrupted."
        )

    # 2. No adapter / no IP at all
    if report.adapter is None:
        return make(
            Fault.ADAPTER_HANG, "critical",
            "No active network adapter found."
        )

    # 3. APIPA address — DHCP has failed
    if report.apipa_detected:
        return make(
            Fault.APIPA_DHCP_FAILURE, "critical",
            f"APIPA address ({report.adapter.ip_address}) detected — DHCP server unreachable."
        )

    # 4. Gateway completely unreachable
    # Many routers and office gateways simply ignore ping, so a silent gateway
    # only counts as a fault when the internet is unreachable too.
    gateway_silent = report.no_gateway or (
        report.gateway_ping is not None and not report.gateway_ping.reachable
    )
    if gateway_silent and not report.has_internet:
        return make(
            Fault.GATEWAY_UNREACHABLE, "critical",
            "Default gateway is not responding — router or local link failure."
        )

    # 5. Wi-Fi: critical signal
    if wifi and wifi.is_connected and wifi.is_critical_signal:
        return make(
            Fault.WIFI_WEAK_SIGNAL, "critical",
            f"Critically weak Wi-Fi signal ({wifi.signal_pct}% / ≈{wifi.rssi_dbm}dBm). "
            "Device is barely within range."
        )

    # 6. ISP / upstream fully down
    if report.has_local_connectivity and not report.has_internet:
        return make(
            Fault.ISP_UPSTREAM_FAILURE, "critical",
            "Local network is reachable but public internet is not. "
            "ISP or upstream router failure."
        )

    # 7. DNS failure (internet IPs work but names don't resolve)
    if report.has_internet and not report.has_dns:
        return make(
            Fault.DNS_FAILURE, "degraded",
            "Internet is reachable by IP but DNS resolution is failing."
        )

    # 8. Severe packet loss
    if report.packet_loss_severe:
        loss = report.internet_ping.loss_pct if report.internet_ping else 0
        return make(
            Fault.HIGH_PACKET_LOSS, "degraded",
            f"Severe packet loss detected ({loss:.0f}%). Network heavily congested."
        )

    # 9. Wi-Fi signal weak (but not critical)
    if wifi and wifi.is_connected and wifi.is_weak_signal:
        return make(
            Fault.WIFI_WEAK_SIGNAL, "degraded",
            f"Weak Wi-Fi signal ({wifi.signal_pct}% / ≈{wifi.rssi_dbm}dBm). "
            "Below recommended threshold."
        )

    # 10. 2.4 GHz + slow link → congestion risk
    if wifi and wifi.is_connected and wifi.is_24ghz and wifi.is_slow_link:
        return make(
            Fault.WIFI_24GHZ_CONGESTION, "degraded",
            f"2.4 GHz band with slow link ({wifi.transmit_rate_mbps} Mbps Tx). "
            "Likely channel congestion or interference."
        )

    # 11. Degraded packet loss
    if report.packet_loss_degraded:
        loss = report.internet_ping.loss_pct if report.internet_ping else 0
        return make(
            Fault.HIGH_PACKET_LOSS, "degraded",
            f"Elevated packet loss ({loss:.0f}%). Connection degraded."
        )

    # 12. Very high latency
    if report.latency_very_high:
        ms = report.internet_ping.avg_ms if report.internet_ping else 0
        return make(
            Fault.HIGH_LATENCY, "degraded",
            f"Very high latency detected ({ms:.0f}ms avg). "
            "Upstream congestion or ISP instability."
        )

    # 13. Partial internet (some loss, high latency, but alive)
    if report.has_internet and (report.latency_high or report.packet_loss_degraded):
        return make(
            Fault.PARTIAL_INTERNET, "advisory",
            "Internet reachable but with elevated latency or minor packet loss."
        )

    # 14. 2.4 GHz only (advisory — no hard problem yet)
    if wifi and wifi.is_connected and wifi.is_24ghz:
        return make(
            Fault.WIFI_24GHZ_CONGESTION, "advisory",
            "On 2.4 GHz Wi-Fi. Susceptible to interference; 5 GHz preferred."
        )

    # 15. Everything is healthy
    return make(
        Fault.HEALTHY, "healthy",
        "All diagnostic tests passed. Network appears healthy."
    )
