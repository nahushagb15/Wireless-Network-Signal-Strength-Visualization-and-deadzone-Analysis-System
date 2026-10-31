"""Scanner client: scans Wi-Fi + pings, and POSTs readings to the Flask API.

Typical use (on the laptop that walks around):

    python -m scanner.scanner --api http://192.168.1.20:5000 --session 3

Position comes from the web UI: whoever clicks the blueprint sets "where the
laptop is now". Two modes:

  continuous  (default) measure every --interval seconds at the latest clicked position
  checkpoint  measure only right after a new click (--samples scan cycles per click)
"""
from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone

import requests

from .ping import measure_link
from .wifi import ScanError, SimulatedScanner, make_scanner, simulated_link


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.http = requests.Session()

    def get(self, path):
        r = self.http.get(self.base + path, timeout=8)
        return r

    def post(self, path, json):
        return self.http.post(self.base + path, json=json, timeout=8)


def default_gateway():
    """Router IP (so ping measures Wi-Fi only). None if it can't be found."""
    ipv4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
    try:
        if platform.system() == "Windows":
            lines = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=10).stdout.splitlines()
            for i, line in enumerate(lines):
                if "Default Gateway" in line:
                    for cand in (line, lines[i + 1] if i + 1 < len(lines) else ""):
                        m = ipv4.search(cand.split(":", 1)[-1])
                        if m:
                            return m.group(1)
        else:
            out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"default via " + ipv4.pattern, out)
            if m:
                return m.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def utc_iso():
    return datetime.now(timezone.utc).isoformat()


def take_sample(scanner, pos, args, sim=False):
    """One scan cycle -> API payload, or None if the scan itself failed."""
    try:
        nets = scanner.scan(pos)
    except ScanError as e:
        log(f"scan failed: {e}")
        return None

    if sim:
        best = max((n.rssi for n in nets), default=None)
        latency, loss = simulated_link(best)
    else:
        latency, loss = measure_link(args.ping_host, args.ping_count, args.ping_timeout)

    ts = utc_iso()
    if not nets:   # no Wi-Fi heard at all: record it as a deadzone reading
        readings = [{"no_signal": True, "latency": latency, "packet_loss": loss}]
    else:
        # latency/loss describe the connected link -> attach to that AP only
        # (if we can't tell which one is connected, attach to every row)
        any_connected = any(n.connected for n in nets)
        readings = []
        for n in nets:
            attach = n.connected or not any_connected
            readings.append({
                "ssid": n.ssid, "bssid": n.bssid, "rssi": round(n.rssi, 1),
                "latency": latency if attach else None,
                "packet_loss": loss if attach else None,
            })
    return {"session_id": args.session, "x": pos[0], "y": pos[1],
            "timestamp": ts, "measurements": readings}


def flush(api, pending):
    """Send queued payloads. Returns False if the session was stopped/deleted."""
    while pending:
        payload = pending[0]
        try:
            r = api.post("/api/measurements", payload)
        except requests.RequestException as e:
            log(f"server unreachable ({e.__class__.__name__}); {len(pending)} cycle(s) queued, will retry")
            return True
        if r.status_code == 201:
            pending.popleft()
        elif r.status_code in (404, 409):
            log("server says: " + r.json().get("error", r.text))
            return False
        else:
            log(f"server rejected a cycle ({r.status_code}): {r.text[:200]} - dropping it")
            pending.popleft()
    return True


def current_position(api, sid):
    try:
        r = api.get(f"/api/sessions/{sid}/position")
    except requests.RequestException as e:
        log(f"cannot reach server: {e.__class__.__name__}")
        return None
    if r.status_code != 200:
        log("server says: " + r.json().get("error", r.text))
        raise SystemExit(1)
    return r.json()


def walk_path(width, height, rows=3, cols=6):
    """Serpentine path used by --auto-walk (simulation only)."""
    pts = []
    for i in range(rows):
        y = height * (i + 0.5) / rows
        xs = [width * (j + 0.5) / cols for j in range(cols)]
        pts += [(x, y) for x in (xs if i % 2 == 0 else xs[::-1])]
    return pts


def main(argv=None):
    p = argparse.ArgumentParser(description="Wi-Fi survey scanner")
    p.add_argument("--api", default="http://127.0.0.1:5000", help="server URL")
    p.add_argument("--session", type=int, default=None,
                   help="session id (default: your newest active session)")
    p.add_argument("--mode", choices=["continuous", "checkpoint"], default="continuous")
    p.add_argument("--interval", type=float, default=3.0, help="seconds between cycles (continuous)")
    p.add_argument("--samples", type=int, default=3, help="scan cycles per click (checkpoint)")
    p.add_argument("--backend", choices=["auto", "netsh", "nmcli", "pywifi"], default="auto")
    p.add_argument("--ping-host", default=None,
                   help="host to ping (default: your router, found automatically)")
    p.add_argument("--ping-count", type=int, default=4)
    p.add_argument("--ping-timeout", type=float, default=1.0)
    p.add_argument("--simulate", action="store_true", help="fake Wi-Fi data (no hardware needed)")
    p.add_argument("--auto-walk", action="store_true",
                   help="with --simulate: walk a path automatically instead of waiting for clicks")
    args = p.parse_args(argv)

    api = Api(args.api)
    if args.session is None:
        try:
            lst = api.get("/api/sessions").json()
        except requests.RequestException as e:
            sys.exit(f"Cannot reach {args.api}: {e.__class__.__name__}. Is the server running? (run start.bat)")
        usable = [s for s in lst if s["status"] == "active" and s["has_blueprint"]]
        if not usable:
            sys.exit("No active session with a floor plan. Create one in the web page first.")
        args.session = usable[0]["id"]
    if args.ping_host is None:
        args.ping_host = default_gateway() or "8.8.8.8"
        log(f"pinging {args.ping_host}")
    try:
        r = api.get(f"/api/sessions/{args.session}")
    except requests.RequestException as e:
        sys.exit(f"Cannot reach {args.api}: {e.__class__.__name__}. Is the server running?")
    if r.status_code != 200:
        sys.exit(r.json().get("error", r.text))
    sess = r.json()
    if sess["status"] != "active":
        sys.exit(f"Session '{sess['name']}' is stopped. Resume it in the web UI first.")
    if not sess["has_blueprint"]:
        sys.exit("Upload a blueprint for this session in the web UI first.")

    if args.simulate:
        scanner = SimulatedScanner(sess["blueprint_width"], sess["blueprint_height"])
    else:
        try:
            scanner = make_scanner(args.backend)
        except ScanError as e:
            sys.exit(str(e))
    log(f"session '{sess['name']}' | backend={scanner.name} | mode={args.mode}")

    pending: deque = deque(maxlen=500)
    try:
        if args.simulate and args.auto_walk:
            return auto_walk(api, args, scanner, sess, pending)
        live_loop(api, args, scanner, pending)
    except KeyboardInterrupt:
        flush(api, pending)
        log("stopped by user")


def live_loop(api, args, scanner, pending):
    last_seq = None
    waiting_logged = False
    while True:
        started = time.time()
        pos = current_position(api, args.session)
        if pos is not None:
            if pos["status"] != "active":
                log("session stopped in the UI - exiting")
                return
            if pos["x"] is None:
                if not waiting_logged:
                    log("waiting for a click on the blueprint...")
                    waiting_logged = True
            else:
                waiting_logged = False
                xy = (pos["x"], pos["y"])
                if args.mode == "continuous":
                    cycles = 1
                else:
                    cycles = args.samples if pos["seq"] != last_seq else 0
                    last_seq = pos["seq"]
                for _ in range(cycles):
                    payload = take_sample(scanner, xy, args, sim=args.simulate)
                    if payload:
                        pending.append(payload)
                        rs = [m.get("rssi") for m in payload["measurements"] if m.get("rssi") is not None]
                        log(f"({xy[0]:.0f},{xy[1]:.0f}) {len(rs)} AP(s), best {max(rs) if rs else 'none'} dBm")
                    if not flush(api, pending):
                        return
        if not flush(api, pending):
            return
        time.sleep(max(0.2, args.interval - (time.time() - started)) if args.mode == "continuous" else 0.7)


def auto_walk(api, args, scanner, sess, pending):
    pts = walk_path(sess["blueprint_width"], sess["blueprint_height"])
    log(f"auto-walk: {len(pts)} checkpoints x {args.samples} samples")
    for x, y in pts:
        r = api.post(f"/api/sessions/{args.session}/position", {"x": x, "y": y})
        if r.status_code != 200:
            sys.exit(r.json().get("error", r.text))
        for _ in range(args.samples):
            payload = take_sample(scanner, (round(x, 1), round(y, 1)), args, sim=True)
            if payload:
                pending.append(payload)
        if not flush(api, pending):
            return
        log(f"checkpoint ({x:.0f},{y:.0f}) done")
        time.sleep(0.3)
    log("auto-walk finished")


if __name__ == "__main__":
    main()
