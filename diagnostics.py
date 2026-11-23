"""
diagnostics.py — Core network diagnostic tests (loopback, gateway, internet, DNS).

All targets (gateway, DNS, public IP) are discovered dynamically.
No hardcoded network values except the public probe IP in config.py.
"""

import re
import socket
import time
from dataclasses import dataclass, field
from typing import Optional

from helpers import run_cmd, log_debug
from config import (
    PING_COUNT, PING_TIMEOUT_MS,
    PACKET_LOSS_WARN_PCT, PACKET_LOSS_BAD_PCT,
    LATENCY_WARN_MS, LATENCY_BAD_MS,
    DNS_TEST_HOST, DNS_TIMEOUT_S,
    PUBLIC_PROBE_HOST, FALLBACK_PROBE_HOST,
    APIPA_PREFIX,
)

# Strict IPv4 match, so an IPv6 value like "2001:db8::1" is never read as "2001".
_IPV4 = r"(\d{1,3}(?:\.\d{1,3}){3})(?![\d.:])"


# Data structures

@dataclass
class PingResult:
    target: str
    sent: int        = 0
    received: int    = 0
    loss_pct: float  = 0.0
    avg_ms: float    = 0.0
    min_ms: float    = 0.0
    max_ms: float    = 0.0
    reachable: bool  = False
    timed_out: bool  = False


@dataclass
class AdapterInfo:
    name: str            = ""
    ip_address: str      = ""
    subnet_mask: str     = ""
    gateway: str         = ""
    dhcp_server: str     = ""
    dns_servers: list[str] = field(default_factory=list)
    mac_address: str     = ""
    dhcp_enabled: bool   = True
    is_apipa: bool       = False
    media_type: str      = ""    # "Wi-Fi" / "Ethernet" / "Unknown"


@dataclass
class DiagnosticReport:
    # Adapter
    adapter: Optional[AdapterInfo]  = None

    # Test results
    loopback_ok: bool               = False
    gateway_ping: Optional[PingResult]  = None
    internet_ping: Optional[PingResult] = None
    dns_ok: bool                    = False
    dns_latency_ms: float           = 0.0

    # Derived flags (set by run_all)
    has_local_connectivity: bool    = False
    has_internet: bool              = False
    has_dns: bool                   = False
    packet_loss_severe: bool        = False
    packet_loss_degraded: bool      = False
    latency_high: bool              = False
    latency_very_high: bool         = False
    apipa_detected: bool            = False
    no_gateway: bool                = False


# ipconfig parser

def _parse_ipconfig() -> list[AdapterInfo]:
    """
    Parse 'ipconfig /all' and return a list of AdapterInfo for each active
    interface that has an IPv4 address. Fully dynamic — no hardcoded names.
    """
    rc, stdout, _ = run_cmd(["ipconfig", "/all"])
    if rc != 0 or not stdout:
        return []

    adapters: list[AdapterInfo] = []
    current: Optional[AdapterInfo] = None
    media_hint = ""
    last_field = ""   # which multi-value field a continuation line belongs to

    for line in stdout.splitlines():
        # New adapter section
        adapter_header = re.match(r"^(\S.+?adapter\s+(.+?)):?\s*$", line, re.IGNORECASE)
        if adapter_header:
            if current and current.ip_address:
                adapters.append(current)
            current = AdapterInfo()
            last_field = ""
            header_text = adapter_header.group(1).lower()
            if "wi-fi" in header_text or "wireless" in header_text:
                media_hint = "Wi-Fi"
            elif "ethernet" in header_text or "local area" in header_text:
                media_hint = "Ethernet"
            else:
                media_hint = "Unknown"
            current.media_type = media_hint
            current.name = adapter_header.group(2).strip()
            continue

        if current is None:
            continue

        def _val(pattern: str) -> str:
            m = re.search(pattern, line, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        # Continuation lines carry extra values for the previous field, e.g.
        # an IPv4 gateway listed under an IPv6 one, or a second DNS server.
        cont = re.match(rf"^\s{{20,}}{_IPV4}\s*$", line)
        if cont:
            value = cont.group(1)
            if last_field == "gateway" and not current.gateway:
                current.gateway = value
            elif last_field == "dns" and value not in current.dns_servers:
                current.dns_servers.append(value)
            continue

        if re.match(r"^\s{1,6}\S", line):
            lowered = line.lower()
            if "default gateway" in lowered:
                last_field = "gateway"
            elif "dns servers" in lowered:
                last_field = "dns"
            else:
                last_field = ""

        ip = _val(rf"IPv4 Address[^:]*:\s*{_IPV4}")
        if ip:
            current.ip_address = ip
            current.is_apipa   = ip.startswith(APIPA_PREFIX)

        gw = _val(rf"Default Gateway[^:]*:\s*{_IPV4}")
        if gw:
            current.gateway = gw

        dns = _val(rf"DNS Servers[^:]*:\s*{_IPV4}")
        if dns and dns not in current.dns_servers:
            current.dns_servers.append(dns)

        dhcp = _val(rf"DHCP Server[^:]*:\s*{_IPV4}")
        if dhcp:
            current.dhcp_server = dhcp

        mac = _val(r"Physical Address[^:]*:\s*([\dA-Fa-f\-]{17})")
        if mac:
            current.mac_address = mac

        mask = _val(r"Subnet Mask[^:]*:\s*([\d.]+)")
        if mask:
            current.subnet_mask = mask

        if re.search(r"DHCP Enabled[^:]*:\s*No", line, re.IGNORECASE):
            current.dhcp_enabled = False

    if current and current.ip_address:
        adapters.append(current)

    log_debug(f"ipconfig parsed {len(adapters)} active adapter(s).")
    for a in adapters:
        log_debug(
            f"  Adapter '{a.name}' [{a.media_type}]  ip={a.ip_address}  "
            f"gw={a.gateway}  dhcp={a.dhcp_server}  dns={a.dns_servers}  "
            f"apipa={a.is_apipa}"
        )
    return adapters


def get_primary_adapter() -> Optional[AdapterInfo]:
    """
    Return the most relevant active adapter:
    prefer the one with a gateway, prefer non-APIPA, prefer real IPs.
    Wi-Fi takes priority over loopback/tunnel adapters.
    """
    adapters = _parse_ipconfig()
    if not adapters:
        return None

    # Filter out loopback, tunnel, virtual
    real = [
        a for a in adapters
        if a.ip_address
        and not a.ip_address.startswith("127.")
        and not a.ip_address.startswith("::1")
        and "loopback" not in a.name.lower()
        and "tunnel" not in a.name.lower()
        and "teredo" not in a.name.lower()
        and "isatap" not in a.name.lower()
    ]

    if not real:
        return adapters[0] if adapters else None

    # Score: has gateway (+3), not APIPA (+2), has DHCP server (+1)
    def score(a: AdapterInfo) -> int:
        s = 0
        if a.gateway:     s += 3
        if not a.is_apipa: s += 2
        if a.dhcp_server:  s += 1
        return s

    real.sort(key=score, reverse=True)
    return real[0]


# Ping helper

def _ping(target: str, count: int = PING_COUNT) -> PingResult:
    result = PingResult(target=target, sent=count)
    rc, stdout, _ = run_cmd(
        ["ping", "-n", str(count), "-w", str(PING_TIMEOUT_MS), target],
        timeout=count * 4,
    )

    # Only replies carrying a TTL are real echo replies. Windows also counts
    # "Destination host unreachable" messages as received, which would make a
    # dead router look reachable.
    real_replies = len(re.findall(r"\bTTL=\d+", stdout, re.IGNORECASE))

    loss_m = re.search(r"Lost\s*=\s*(\d+)\s*\((\d+)%", stdout, re.IGNORECASE)
    if loss_m:
        lost = int(loss_m.group(1))
        result.received = min(count - lost, real_replies)
    elif rc == 0:
        result.received = min(count, real_replies)
    result.loss_pct  = round(100.0 * (count - result.received) / count, 1) if count else 100.0
    result.reachable = result.received > 0

    # Parse latency
    lat_m = re.search(
        r"Minimum\s*=\s*(\d+)ms.*?Maximum\s*=\s*(\d+)ms.*?Average\s*=\s*(\d+)ms",
        stdout, re.IGNORECASE | re.DOTALL,
    )
    if lat_m:
        result.min_ms = float(lat_m.group(1))
        result.max_ms = float(lat_m.group(2))
        result.avg_ms = float(lat_m.group(3))
    elif "request timed out" in stdout.lower() and result.received == 0:
        result.timed_out = True

    log_debug(
        f"  ping {target} x{count} → reachable={result.reachable}  "
        f"loss={result.loss_pct:.0f}%  avg={result.avg_ms:.0f}ms"
    )
    return result


# Individual tests

def test_loopback() -> bool:
    """Test 1 — Windows TCP/IP stack via 127.0.0.1."""
    log_debug("Test 1: Loopback (127.0.0.1)")
    r = _ping("127.0.0.1", count=3)
    return r.reachable


def test_gateway(gateway_ip: str) -> PingResult:
    """Test 2 — Local router/AP reachability."""
    log_debug(f"Test 2: Gateway ({gateway_ip})")
    return _ping(gateway_ip, count=PING_COUNT)


def test_internet() -> PingResult:
    """Test 3 — Public internet reachability (Cloudflare, then Google)."""
    log_debug(f"Test 3: Internet ({PUBLIC_PROBE_HOST})")
    r = _ping(PUBLIC_PROBE_HOST, count=PING_COUNT)
    if not r.reachable:
        log_debug(f"Test 3b: Fallback internet ({FALLBACK_PROBE_HOST})")
        r2 = _ping(FALLBACK_PROBE_HOST, count=PING_COUNT)
        if r2.reachable:
            return r2
    return r


def test_dns() -> tuple[bool, float]:
    """
    Test 4 — DNS resolution via Python socket (no nslookup dependency).
    Returns (success, latency_ms).
    """
    log_debug(f"Test 4: DNS ({DNS_TEST_HOST})")
    try:
        start = time.monotonic()
        socket.setdefaulttimeout(DNS_TIMEOUT_S)
        socket.getaddrinfo(DNS_TEST_HOST, None)
        elapsed = (time.monotonic() - start) * 1000
        log_debug(f"  DNS resolved in {elapsed:.1f}ms")
        return True, elapsed
    except OSError as e:
        log_debug(f"  DNS failed: {e}")
        return False, 0.0


# Master runner

def run_all_diagnostics() -> DiagnosticReport:
    """
    Run the tests in sequence and return a populated DiagnosticReport.
    Stops early if loopback fails, since nothing else can work then.
    """
    report = DiagnosticReport()

    # Adapter discovery
    report.adapter = get_primary_adapter()
    if report.adapter:
        report.apipa_detected = report.adapter.is_apipa
        report.no_gateway     = not report.adapter.gateway

    # Test 1: Loopback
    report.loopback_ok = test_loopback()
    if not report.loopback_ok:
        log_debug("Loopback failed — skipping remaining tests.")
        return report  # Stack is broken; nothing else will work

    # Test 2: Gateway
    if report.adapter and report.adapter.gateway:
        report.gateway_ping = test_gateway(report.adapter.gateway)
        report.has_local_connectivity = report.gateway_ping.reachable
    else:
        log_debug("No gateway found — skipping gateway ping.")
        report.has_local_connectivity = False

    # Test 3: Internet
    report.internet_ping = test_internet()
    report.has_internet  = report.internet_ping.reachable

    # Test 4: DNS
    report.dns_ok, report.dns_latency_ms = test_dns()
    report.has_dns = report.dns_ok

    # Derived quality flags
    ref = report.internet_ping if report.has_internet else report.gateway_ping
    if ref:
        report.packet_loss_severe   = ref.loss_pct >= PACKET_LOSS_BAD_PCT
        report.packet_loss_degraded = ref.loss_pct >= PACKET_LOSS_WARN_PCT
        report.latency_high         = ref.avg_ms   >= LATENCY_WARN_MS
        report.latency_very_high    = ref.avg_ms   >= LATENCY_BAD_MS

    return report
