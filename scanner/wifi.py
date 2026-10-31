"""Wi-Fi scanning backends: Windows (netsh), Linux (nmcli), pywifi, and a simulator.

Every backend returns a list of Network objects. RSSI is in dBm; netsh/nmcli only
report a 0-100 quality, converted with dBm = quality/2 - 100 (the standard mapping).
"""
from __future__ import annotations

import math
import platform
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass


class ScanError(RuntimeError):
    """Scan failed (adapter off, tool missing, permission...). The caller keeps going."""


@dataclass
class Network:
    ssid: str
    bssid: str
    rssi: float
    connected: bool = False


def pct_to_dbm(pct: float) -> float:
    return max(-100.0, min(-30.0, pct / 2.0 - 100.0))


def _norm_mac(mac: str) -> str:
    mac = mac.strip().lower().replace("-", ":")
    if ":" not in mac and len(mac) == 12:
        mac = ":".join(mac[i:i + 2] for i in range(0, 12, 2))
    return mac


def _run(cmd, timeout=20):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise ScanError(f"'{cmd[0]}' not found on this machine.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanError(f"'{cmd[0]}' timed out.") from exc
    return out


# --------------------------------------------------------------------- Windows
_MAC = r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})"


def parse_netsh_networks(text: str):
    """Parse `netsh wlan show networks mode=bssid` (English output)."""
    nets, ssid, bssid = [], None, None
    for line in text.splitlines():
        m = re.match(r"^\s*SSID\s+\d+\s*:\s*(.*)$", line)
        if m:
            ssid, bssid = m.group(1).strip() or "<hidden>", None
            continue
        m = re.match(r"^\s*BSSID\s+\d+\s*:\s*" + _MAC, line)
        if m:
            bssid = m.group(1).lower()
            continue
        m = re.match(r"^\s*Signal\s*:\s*(\d+)\s*%", line)
        if m and ssid is not None and bssid is not None:
            nets.append(Network(ssid, bssid, pct_to_dbm(int(m.group(1)))))
            bssid = None
    return nets


def parse_netsh_connected(text: str):
    """Return the connected BSSID from `netsh wlan show interfaces`, or None."""
    state_ok, bssid = False, None
    for line in text.splitlines():
        if re.match(r"^\s*State\s*:\s*connected", line, re.I):
            state_ok = True
        m = re.match(r"^\s*BSSID\s*:\s*" + _MAC, line)
        if m:
            bssid = m.group(1).lower()
    return bssid if state_ok else None


class NetshScanner:
    name = "netsh"

    def scan(self, pos=None):
        out = _run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        if out.returncode != 0 or not out.stdout.strip():
            raise ScanError("netsh could not list networks (Wi-Fi off, or location permission "
                            "denied in Windows Settings > Privacy > Location).")
        nets = parse_netsh_networks(out.stdout)
        try:
            cur = parse_netsh_connected(_run(["netsh", "wlan", "show", "interfaces"]).stdout)
        except ScanError:
            cur = None
        for n in nets:
            n.connected = cur is not None and n.bssid == cur
        return nets


# ----------------------------------------------------------------------- Linux
def parse_nmcli(text: str):
    """Parse `nmcli -t -f IN-USE,SSID,BSSID,SIGNAL dev wifi list` (terse, ':' escaped as '\\:')."""
    nets = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = re.split(r"(?<!\\):", line)
        if len(parts) != 4:
            continue
        in_use, ssid, bssid, signal = (p.replace("\\:", ":") for p in parts)
        try:
            rssi = pct_to_dbm(float(signal))
        except ValueError:
            continue
        nets.append(Network(ssid.strip() or "<hidden>", _norm_mac(bssid), rssi, in_use.strip() == "*"))
    return nets


class NmcliScanner:
    name = "nmcli"

    def scan(self, pos=None):
        base = ["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,SIGNAL", "dev", "wifi", "list"]
        out = _run(base + ["--rescan", "yes"], timeout=30)
        if out.returncode != 0:                      # rescan throttled -> use cached list
            out = _run(base + ["--rescan", "no"])
        if out.returncode != 0:
            raise ScanError("nmcli failed: " + (out.stderr.strip() or "is Wi-Fi enabled?"))
        return parse_nmcli(out.stdout)


# --------------------------------------------------------------------- pywifi
class PywifiScanner:
    name = "pywifi"

    def __init__(self):
        try:
            import pywifi
        except ImportError as exc:
            raise ScanError("pywifi is not installed (pip install pywifi comtypes).") from exc
        ifaces = pywifi.PyWiFi().interfaces()
        if not ifaces:
            raise ScanError("No Wi-Fi interface found.")
        self.iface = ifaces[0]

    def scan(self, pos=None):
        self.iface.scan()
        time.sleep(3.0)                              # scan is asynchronous
        return [Network(r.ssid or "<hidden>", _norm_mac(r.bssid), float(r.signal))
                for r in self.iface.scan_results()]


# ------------------------------------------------------------------- simulator
class SimulatedScanner:
    """Fake Wi-Fi for testing the pipeline without hardware.

    Two virtual access points on the blueprint; signal follows a log-distance
    model, so far corners naturally become deadzones.
    """
    name = "simulate"

    def __init__(self, width, height, seed=None):
        self.w, self.h = width, height
        self.rng = random.Random(seed)
        self.aps = [  # (ssid, bssid, x, y, tx offset)
            ("CampusNet", "02:00:00:00:00:01", 0.25 * width, 0.30 * height, 0),
            ("CampusNet", "02:00:00:00:00:02", 0.30 * width, 0.80 * height, -3),
            ("LabGuest", "02:00:00:00:00:03", 0.20 * width, 0.25 * height, -6),
        ]

    def scan(self, pos=None):
        if pos is None:
            raise ScanError("No position yet - click the blueprint first.")
        x, y = pos
        nets = []
        for ssid, bssid, ax, ay, off in self.aps:
            metres = max(1.0, math.hypot(x - ax, y - ay) / self.w * 30.0)
            rssi = -35 + off - 30 * math.log10(metres) + self.rng.gauss(0, 2.0)
            if rssi > -92:                           # below this the AP isn't heard
                nets.append(Network(ssid, bssid, round(max(rssi, -92), 1), connected=(bssid.endswith("01"))))
        return nets


def simulated_link(rssi: float | None, rng=random):
    """Latency/loss that degrade as RSSI worsens (for the simulator only)."""
    if rssi is None:
        return None, 100.0
    loss = max(0.0, min(100.0, (-68 - rssi) * 2.2 + rng.gauss(0, 2)))
    latency = 8 + max(0.0, (-65 - rssi)) * 6 + rng.uniform(0, 12)
    return round(latency, 1), round(max(loss, 0.0), 1)


# --------------------------------------------------------------------- factory
def make_scanner(backend="auto"):
    system = platform.system()
    if backend == "netsh" or (backend == "auto" and system == "Windows"):
        return NetshScanner()
    if backend == "nmcli" or (backend == "auto" and system == "Linux" and shutil.which("nmcli")):
        return NmcliScanner()
    if backend == "pywifi" or backend == "auto":
        return PywifiScanner()
    raise ScanError(f"Unknown backend '{backend}'.")
