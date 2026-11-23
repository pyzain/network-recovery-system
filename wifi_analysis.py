"""
wifi_analysis.py — Dynamic Wi-Fi interface inspection.

Parses 'netsh wlan show interfaces' at runtime.
No hardcoded SSIDs, adapter names, or signal thresholds beyond config.py.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from helpers import run_cmd, log_debug
from config import RSSI_WEAK_DBM, RSSI_VERY_WEAK_DBM, SIGNAL_WARN_PCT, LINK_SPEED_WARN_MBPS


@dataclass
class WifiInfo:
    adapter_name: str = ""
    ssid: str = ""
    bssid: str = ""
    state: str = ""                 # e.g. "connected"
    radio_type: str = ""            # e.g. "802.11n"
    band: str = ""                  # "2.4 GHz" / "5 GHz" / "6 GHz"
    channel: str = ""
    receive_rate_mbps: float = 0.0
    transmit_rate_mbps: float = 0.0
    signal_pct: int = 0             # 0–100 Windows signal quality
    rssi_dbm: Optional[int] = None  # estimated from signal_pct
    auth: str = ""
    cipher: str = ""
    hosted_network: str = ""
    raw_lines: list[str] = field(default_factory=list)

    # Derived assessments
    @property
    def is_connected(self) -> bool:
        return self.state.lower() == "connected"

    @property
    def is_weak_signal(self) -> bool:
        if self.rssi_dbm is not None:
            return self.rssi_dbm < RSSI_WEAK_DBM
        return self.signal_pct < SIGNAL_WARN_PCT

    @property
    def is_critical_signal(self) -> bool:
        if self.rssi_dbm is not None:
            return self.rssi_dbm < RSSI_VERY_WEAK_DBM
        return self.signal_pct < 15

    @property
    def is_slow_link(self) -> bool:
        effective = min(self.receive_rate_mbps, self.transmit_rate_mbps)
        return 0 < effective < LINK_SPEED_WARN_MBPS

    @property
    def is_24ghz(self) -> bool:
        return "2.4" in self.band

    @property
    def is_5ghz(self) -> bool:
        return "5" in self.band and "2.4" not in self.band


def _signal_to_rssi(pct: int) -> int:
    """
    Convert Windows signal quality (0–100%) to approximate RSSI dBm.
    Windows maps:  quality% = 2 * (RSSI + 100)  for -100 to -50 dBm range.
    """
    clamped = max(0, min(100, pct))
    return (clamped // 2) - 100


def get_wifi_info() -> Optional[WifiInfo]:
    """
    Parse 'netsh wlan show interfaces' and return a WifiInfo object.
    Returns None if no wireless interface is found or not on Windows.
    """
    rc, stdout, _ = run_cmd(["netsh", "wlan", "show", "interfaces"])
    if rc != 0 or not stdout.strip():
        log_debug("netsh wlan show interfaces returned nothing — likely no Wi-Fi adapter or not Windows.")
        return None

    info = WifiInfo()
    info.raw_lines = stdout.splitlines()

    def _extract(pattern: str, text: str) -> str:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""

    info.adapter_name  = _extract(r"^\s+Name\s*:\s*(.+)$",             stdout)
    info.state         = _extract(r"^\s+State\s*:\s*(.+)$",             stdout)
    info.ssid          = _extract(r"^\s+SSID\s*:\s*(.+)$",              stdout)
    info.bssid         = _extract(r"^\s+BSSID\s*:\s*(.+)$",             stdout)
    info.radio_type    = _extract(r"^\s+Radio type\s*:\s*(.+)$",        stdout)
    info.auth          = _extract(r"^\s+Authentication\s*:\s*(.+)$",    stdout)
    info.cipher        = _extract(r"^\s+Cipher\s*:\s*(.+)$",            stdout)
    info.channel       = _extract(r"^\s+Channel\s*:\s*(.+)$",           stdout)

    # Band — try direct field first, fall back to channel heuristic
    band_raw = _extract(r"^\s+Band\s*:\s*(.+)$", stdout)
    if band_raw:
        info.band = band_raw
    else:
        try:
            ch = int(info.channel)
            info.band = "2.4 GHz" if ch <= 13 else "5 GHz"
        except ValueError:
            info.band = "Unknown"

    # Link speeds
    rx_raw = _extract(r"^\s+Receive\s+rate\s*\(Mbps\)\s*:\s*([\d.]+)", stdout)
    tx_raw = _extract(r"^\s+Transmit\s+rate\s*\(Mbps\)\s*:\s*([\d.]+)", stdout)
    try:
        info.receive_rate_mbps  = float(rx_raw)
        info.transmit_rate_mbps = float(tx_raw)
    except ValueError:
        pass

    # Signal
    sig_raw = _extract(r"^\s+Signal\s*:\s*(\d+)\s*%", stdout)
    try:
        info.signal_pct = int(sig_raw)
        info.rssi_dbm   = _signal_to_rssi(info.signal_pct)
    except ValueError:
        pass

    if not info.adapter_name:
        log_debug("Could not parse adapter name from netsh output.")
        return None

    log_debug(
        f"Wi-Fi → adapter='{info.adapter_name}'  ssid='{info.ssid}'  "
        f"state='{info.state}'  band='{info.band}'  ch={info.channel}  "
        f"signal={info.signal_pct}%  rssi≈{info.rssi_dbm}dBm  "
        f"rx={info.receive_rate_mbps}Mbps  tx={info.transmit_rate_mbps}Mbps"
    )
    return info


def get_available_networks_count() -> int:
    """Return how many APs are visible — useful for roaming/congestion context."""
    rc, stdout, _ = run_cmd(["netsh", "wlan", "show", "networks"])
    if rc != 0:
        return 0
    return len(re.findall(r"SSID\s+\d+\s*:", stdout, re.IGNORECASE))
