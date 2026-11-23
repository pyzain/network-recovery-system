"""
config.py — Thresholds, limits, and tunable constants.
No hardcoded IPs, SSIDs, adapter names, or gateway values.
Everything network-specific is discovered dynamically at runtime.
"""

# Ping / Latency
PING_COUNT            = 6          # packets per test (kept low to avoid spamming)
PING_TIMEOUT_MS       = 2000       # ms per individual ping reply
PACKET_LOSS_WARN_PCT  = 20.0       # % loss → "degraded"
PACKET_LOSS_BAD_PCT   = 60.0       # % loss → "severe"
LATENCY_WARN_MS       = 150.0      # avg latency → "high"
LATENCY_BAD_MS        = 400.0      # avg latency → "very high"

# Wi-Fi Signal
RSSI_WEAK_DBM         = -72        # dBm; below this → weak signal
RSSI_VERY_WEAK_DBM    = -82        # dBm; below this → critical
SIGNAL_WARN_PCT       = 35         # % quality warn threshold
LINK_SPEED_WARN_MBPS  = 20         # Mbps; below this → unstable link

# DNS
DNS_TEST_HOST         = "google.com"
DNS_TIMEOUT_S         = 4.0        # seconds before DNS considered failed

# Public reachability (used only once per run)
PUBLIC_PROBE_HOST     = "1.1.1.1"  # Cloudflare — no DNS dependency
FALLBACK_PROBE_HOST   = "8.8.8.8"  # Google — fallback

# APIPA detection
APIPA_PREFIX          = "169.254." # auto-assigned → DHCP failure

# Safety guards
MAX_DHCP_ATTEMPTS     = 1          # never loop DHCP renewals
ADAPTER_RESET_WAIT_S  = 5          # seconds to wait after disabling adapter
WIFI_RECONNECT_WAIT_S = 4          # seconds after disconnect before reconnect

# Debug output
TIMESTAMP_FORMAT      = "%Y-%m-%d %H:%M:%S"
