"""Latency and packet-loss measurement with ping3 (falls back to the system ping)."""
from __future__ import annotations

import platform
import re
import subprocess
import time

try:
    import ping3
    ping3.EXCEPTIONS = False
except ImportError:  # the system ping fallback still works
    ping3 = None


def _system_ping_once(host, timeout):
    if platform.system() == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
    return float(m.group(1)) if m else None


def ping_once(host, timeout=1.0):
    """Round-trip time in ms, or None on timeout/unreachable."""
    if ping3 is not None:
        try:
            r = ping3.ping(host, timeout=timeout, unit="ms")
            if r is None or r is False:
                return None
            return float(r)
        except (PermissionError, OSError):
            pass  # raw sockets not allowed -> use the system ping binary
    return _system_ping_once(host, timeout)


def measure_link(host, count=4, timeout=1.0, gap=0.15):
    """Return (avg_latency_ms | None, packet_loss_percent)."""
    rtts = []
    for i in range(count):
        r = ping_once(host, timeout)
        if r is not None:
            rtts.append(r)
        if i < count - 1:
            time.sleep(gap)
    loss = 100.0 * (count - len(rtts)) / count
    avg = round(sum(rtts) / len(rtts), 2) if rtts else None
    return avg, round(loss, 1)
