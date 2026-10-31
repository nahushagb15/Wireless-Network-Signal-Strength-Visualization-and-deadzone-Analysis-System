"""Deadzone detection + interpolation. Pure Python/numpy: no Flask or DB imports,
so it can be unit-tested with plain lists of dicts.

A *reading row* is a dict with: x, y, timestamp, ssid, bssid, rssi, latency,
packet_loss, no_signal.  All rows from one scan cycle share (x, y, timestamp).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, asdict

import numpy as np

NO_SIGNAL_RSSI = -100.0  # value used when a network (or any network) is not heard at all

METRICS = ("rssi", "latency", "packet_loss")
_POINT_KEY = {"rssi": "avg_rssi", "latency": "avg_latency", "packet_loss": "avg_packet_loss"}


@dataclass
class Thresholds:
    rssi: float = -75.0          # dBm: weaker than this is a weak reading
    strong_rssi: float = -60.0   # dBm: at or above this is "strong"
    packet_loss: float = 10.0    # percent: more than this is a weak reading
    latency: float = 150.0       # ms: more than this is a weak reading
    min_readings: int = 3        # scan cycles needed before a spot can be called a deadzone
    weak_fraction: float = 0.6   # share of weak cycles that makes a spot a deadzone

    def to_dict(self):
        return asdict(self)


# ----------------------------------------------------------------- aggregation
def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _matches(row, ssid, bssid):
    if row.get("no_signal") or row.get("rssi") is None:
        return False
    if ssid and row.get("ssid") != ssid:
        return False
    if bssid and (row.get("bssid") or "").lower() != bssid.lower():
        return False
    return True


def build_cycles(rows, ssid=None, bssid=None):
    """Collapse raw rows into one reading per scan cycle.

    * Several BSSIDs of the same SSID (multiple APs) -> the strongest one wins,
      which is what a roaming client would associate with.
    * No SSID filter -> strongest of any network (overall coverage).
    * Filtered network not heard in a cycle -> counted as NO_SIGNAL_RSSI there.
    * latency / packet loss only come from rows that belong to the filter
      (they are measured on the connected link, so other SSIDs get none).
    """
    groups = defaultdict(list)
    for r in rows:
        groups[(round(r["x"]), round(r["y"]), r["timestamp"])].append(r)

    cycles = []
    for (x, y, ts), group in groups.items():
        matching = [g for g in group if _matches(g, ssid, bssid)]
        rssi = max(g["rssi"] for g in matching) if matching else NO_SIGNAL_RSSI
        link_rows = [g for g in group if g.get("no_signal") or _matches(g, ssid, bssid)]
        cycles.append({
            "x": x, "y": y, "timestamp": ts, "rssi": rssi,
            "latency": _mean(g.get("latency") for g in link_rows),
            "packet_loss": _mean(g.get("packet_loss") for g in link_rows),
        })
    return cycles


def weak_reasons(cycle, th: Thresholds):
    reasons = []
    if cycle["rssi"] < th.rssi:
        reasons.append("signal")
    if cycle["packet_loss"] is not None and cycle["packet_loss"] > th.packet_loss:
        reasons.append("packet_loss")
    if cycle["latency"] is not None and cycle["latency"] > th.latency:
        reasons.append("latency")
    return reasons


def aggregate_points(rows, th: Thresholds, ssid=None, bssid=None):
    """One point per clicked location with averaged metrics and a status."""
    by_loc = defaultdict(list)
    for c in build_cycles(rows, ssid, bssid):
        by_loc[(c["x"], c["y"])].append(c)

    points = []
    for (x, y), cycles in by_loc.items():
        n = len(cycles)
        reason_counts = defaultdict(int)
        weak = 0
        for c in cycles:
            rs = weak_reasons(c, th)
            if rs:
                weak += 1
            for r in rs:
                reason_counts[r] += 1
        weak_frac = weak / n
        avg_rssi = _mean(c["rssi"] for c in cycles)

        if n >= th.min_readings and weak_frac >= th.weak_fraction:
            status = "deadzone"
        elif weak_frac >= th.weak_fraction:
            status = "suspect"      # looks weak but too few readings to be sure
        elif avg_rssi >= th.strong_rssi:
            status = "strong"
        else:
            status = "moderate"

        points.append({
            "x": x, "y": y, "n": n,
            "avg_rssi": round(avg_rssi, 1),
            "min_rssi": round(min(c["rssi"] for c in cycles), 1),
            "avg_latency": _round(_mean(c["latency"] for c in cycles)),
            "avg_packet_loss": _round(_mean(c["packet_loss"] for c in cycles)),
            "weak_fraction": round(weak_frac, 2),
            "status": status,
            "reasons": sorted(reason_counts, key=lambda k: -reason_counts[k]),
        })
    points.sort(key=lambda p: (p["y"], p["x"]))
    return points


def _round(v, nd=1):
    return None if v is None else round(v, nd)


# ------------------------------------------------------------------ deadzones
def cluster_deadzones(points, eps):
    """Group neighbouring deadzone points (within eps px) into regions."""
    dz = [p for p in points if p["status"] == "deadzone"]
    parent = list(range(len(dz)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(dz)):
        for j in range(i + 1, len(dz)):
            if math.dist((dz[i]["x"], dz[i]["y"]), (dz[j]["x"], dz[j]["y"])) <= eps:
                parent[find(i)] = find(j)

    comps = defaultdict(list)
    for i, p in enumerate(dz):
        comps[find(i)].append(p)

    regions = []
    for members in comps.values():
        cx = sum(p["x"] for p in members) / len(members)
        cy = sum(p["y"] for p in members) / len(members)
        radius = max(math.dist((cx, cy), (p["x"], p["y"])) for p in members) + eps * 0.4
        reasons = defaultdict(int)
        for p in members:
            for r in p["reasons"]:
                reasons[r] += 1
        regions.append({
            "cx": round(cx, 1), "cy": round(cy, 1), "radius": round(radius, 1),
            "points": len(members),
            "avg_rssi": round(sum(p["avg_rssi"] for p in members) / len(members), 1),
            "worst_rssi": min(p["min_rssi"] for p in members),
            "reasons": sorted(reasons, key=lambda k: -reasons[k]),
        })
    regions.sort(key=lambda r: (-r["points"], r["cx"]))
    for i, r in enumerate(regions, 1):
        r["id"] = i
    return regions


# --------------------------------------------------------------- interpolation
def interpolate_grid(points, width, height, metric="rssi", grid_w=90,
                     power=2.0, reach_frac=0.18):
    """Inverse-distance-weighted grid of one metric over the blueprint.

    Cells farther than reach_frac * diagonal from every measured point are left
    empty (None) so unexplored areas are not painted with made-up data.
    """
    key = _POINT_KEY[metric]
    pts = [(p["x"], p["y"], p[key]) for p in points if p.get(key) is not None]
    grid_h = max(8, round(grid_w * height / width))
    xs = (np.arange(grid_w) + 0.5) * width / grid_w
    ys = (np.arange(grid_h) + 0.5) * height / grid_h
    if not pts:
        return {"xs": xs.round(1).tolist(), "ys": ys.round(1).tolist(),
                "z": [[None] * grid_w for _ in range(grid_h)]}

    P = np.array([(a, b) for a, b, _ in pts], dtype=float)
    V = np.array([v for _, _, v in pts], dtype=float)
    gx, gy = np.meshgrid(xs, ys)
    cells = np.column_stack([gx.ravel(), gy.ravel()])
    reach = reach_frac * math.hypot(width, height)

    z = np.empty(len(cells))
    for s in range(0, len(cells), 1000):          # chunked to cap memory
        chunk = cells[s:s + 1000]
        d = np.linalg.norm(chunk[:, None, :] - P[None, :, :], axis=2)
        w = 1.0 / np.maximum(d, 1e-6) ** power
        zc = (w * V).sum(axis=1) / w.sum(axis=1)
        zc[d.min(axis=1) > reach] = np.nan
        z[s:s + 1000] = zc
    z = z.reshape(grid_h, grid_w)
    z_list = [[None if math.isnan(v) else round(float(v), 2) for v in row] for row in z]
    return {"xs": xs.round(1).tolist(), "ys": ys.round(1).tolist(), "z": z_list}


# ------------------------------------------------------------------- colouring
RED, YELLOW, GREEN = (211, 47, 47), (249, 192, 38), (46, 160, 67)


def metric_spec(metric, th: Thresholds):
    """Colour mapping for one metric: `bad` -> red, `good` -> green, midpoint yellow."""
    if metric == "rssi":
        spec = dict(label="Signal strength", unit="dBm", bad=th.rssi, good=th.strong_rssi,
                    zmin=-100.0, zmax=-30.0)
    elif metric == "latency":
        spec = dict(label="Latency", unit="ms", bad=th.latency, good=th.latency / 3,
                    zmin=0.0, zmax=th.latency * 2)
    elif metric == "packet_loss":
        spec = dict(label="Packet loss", unit="%", bad=th.packet_loss, good=th.packet_loss / 10,
                    zmin=0.0, zmax=th.packet_loss * 3)
    else:
        raise ValueError(f"unknown metric '{metric}'")
    spec["metric"] = metric
    spec["colorscale"] = _plotly_colorscale(spec)
    return spec


def _hex(c):
    return "#%02x%02x%02x" % c


def _plotly_colorscale(spec):
    lo, hi = spec["zmin"], spec["zmax"]

    def pos(v):
        return min(1.0, max(0.0, (v - lo) / (hi - lo)))

    mid = (spec["bad"] + spec["good"]) / 2
    stops = sorted([(pos(spec["bad"]), RED), (pos(mid), YELLOW), (pos(spec["good"]), GREEN)],
                   key=lambda s: s[0])
    out = []
    for p, c in stops:
        if out and p <= out[-1][0]:
            p = min(1.0, out[-1][0] + 1e-4)
        out.append([round(p, 4), _hex(c)])
    if out[0][0] > 0:
        out.insert(0, [0, out[0][1]])
    if out[-1][0] < 1:
        out.append([1, out[-1][1]])
    return out


def colorize(values, spec):
    """numpy array of metric values -> float RGB array (..., 3)."""
    t = np.clip((np.asarray(values, dtype=float) - spec["bad"]) / (spec["good"] - spec["bad"]), 0, 1)
    red, yel, grn = (np.array(c, dtype=float) for c in (RED, YELLOW, GREEN))
    lo = red + (yel - red) * (t[..., None] * 2)
    hi = yel + (grn - yel) * ((t[..., None] - 0.5) * 2)
    return np.where(t[..., None] < 0.5, lo, hi)


# ------------------------------------------------------------------ one-call API
def run_analysis(rows, width, height, th: Thresholds, ssid=None, bssid=None, metric="rssi"):
    points = aggregate_points(rows, th, ssid, bssid)
    eps = 0.08 * math.hypot(width, height)
    regions = cluster_deadzones(points, eps)
    grid = interpolate_grid(points, width, height, metric)
    counts = defaultdict(int)
    for p in points:
        counts[p["status"]] += 1
    n_points = len(points)
    summary = {
        "locations": n_points,
        "readings": sum(p["n"] for p in points),
        "strong": counts["strong"], "moderate": counts["moderate"],
        "suspect": counts["suspect"], "deadzone": counts["deadzone"],
        "deadzone_regions": len(regions),
        "coverage_pct": round(100 * (n_points - counts["deadzone"]) / n_points, 1) if n_points else None,
        "avg_rssi": round(sum(p["avg_rssi"] for p in points) / n_points, 1) if n_points else None,
    }
    return {
        "points": points, "regions": regions, "grid": grid, "summary": summary,
        "spec": metric_spec(metric, th), "thresholds": th.to_dict(),
        "blueprint": {"width": width, "height": height},
    }
