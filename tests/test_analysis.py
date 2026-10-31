from backend.analysis import (NO_SIGNAL_RSSI, Thresholds, aggregate_points, build_cycles,
                              cluster_deadzones, interpolate_grid, metric_spec, run_analysis)

TH = Thresholds()


def cycle(x, y, ts, readings):
    """readings: list of (ssid, bssid, rssi[, latency, loss])"""
    rows = []
    for r in readings:
        ssid, bssid, rssi = r[:3]
        lat = r[3] if len(r) > 3 else None
        loss = r[4] if len(r) > 4 else None
        rows.append(dict(x=x, y=y, timestamp=ts, ssid=ssid, bssid=bssid, rssi=rssi,
                         latency=lat, packet_loss=loss, no_signal=False))
    return rows


def test_strongest_bssid_wins_for_same_ssid():
    rows = cycle(10, 10, 1, [("Net", "aa", -80), ("Net", "bb", -55), ("Other", "cc", -40)])
    c = build_cycles(rows, ssid="Net")
    assert len(c) == 1 and c[0]["rssi"] == -55


def test_no_filter_uses_best_of_any_network():
    rows = cycle(10, 10, 1, [("Net", "aa", -80), ("Other", "cc", -40)])
    assert build_cycles(rows)[0]["rssi"] == -40


def test_ssid_not_heard_counts_as_no_signal():
    rows = cycle(10, 10, 1, [("Other", "cc", -40)])
    assert build_cycles(rows, ssid="Net")[0]["rssi"] == NO_SIGNAL_RSSI


def test_no_signal_row_is_deadzone_reading():
    rows = [dict(x=5, y=5, timestamp=t, ssid=None, bssid=None, rssi=None, latency=None,
                 packet_loss=100.0, no_signal=True) for t in (1, 2, 3)]
    pts = aggregate_points(rows, TH)
    assert pts[0]["status"] == "deadzone" and pts[0]["avg_rssi"] == NO_SIGNAL_RSSI


def test_needs_repeated_weak_readings():
    rows = cycle(0, 0, 1, [("N", "a", -85)]) + cycle(0, 0, 2, [("N", "a", -86)])
    assert aggregate_points(rows, TH)[0]["status"] == "suspect"      # only 2 readings
    rows += cycle(0, 0, 3, [("N", "a", -84)])
    assert aggregate_points(rows, TH)[0]["status"] == "deadzone"     # 3 readings


def test_one_bad_reading_does_not_make_a_deadzone():
    rows = []
    for t, rssi in enumerate([-50, -52, -90, -51], 1):
        rows += cycle(0, 0, t, [("N", "a", rssi)])
    p = aggregate_points(rows, TH)[0]
    assert p["status"] == "moderate"      # average -60.75: pulled down, but not a deadzone
    assert p["weak_fraction"] == 0.25


def test_packet_loss_and_latency_trigger_deadzone():
    rows = []
    for t in (1, 2, 3):
        rows += cycle(0, 0, t, [("N", "a", -55, 400, 30)])
    p = aggregate_points(rows, TH)[0]
    assert p["status"] == "deadzone"
    assert set(p["reasons"]) == {"packet_loss", "latency"}


def test_latency_only_from_filtered_network():
    rows = cycle(0, 0, 1, [("Mine", "a", -50, 20, 0), ("Other", "b", -60)])
    assert build_cycles(rows, ssid="Other")[0]["latency"] is None
    assert build_cycles(rows, ssid="Mine")[0]["latency"] == 20


def test_clustering_groups_neighbours():
    pts = [dict(x=x, y=y, status="deadzone", reasons=["signal"], avg_rssi=-85, min_rssi=-90)
           for x, y in [(10, 10), (20, 12), (400, 400)]]
    regions = cluster_deadzones(pts, eps=50)
    assert sorted(r["points"] for r in regions) == [1, 2]
    assert [r["id"] for r in regions] == [1, 2]


def test_interpolation_masks_far_cells_and_hits_value_near_point():
    pts = [dict(x=100, y=100, avg_rssi=-50, avg_latency=None, avg_packet_loss=None)]
    g = interpolate_grid(pts, 1000, 800, "rssi", grid_w=50)
    flat = [v for row in g["z"] for v in row]
    assert None in flat and any(v is not None for v in flat)
    assert all(v == -50 for v in flat if v is not None)


def test_empty_session_is_fine():
    r = run_analysis([], 800, 600, TH)
    assert r["summary"]["locations"] == 0 and r["summary"]["coverage_pct"] is None


def test_colorscales_are_valid_for_plotly():
    for m in ("rssi", "latency", "packet_loss"):
        cs = metric_spec(m, TH)["colorscale"]
        positions = [p for p, _ in cs]
        assert positions[0] == 0 and positions[-1] == 1
        assert positions == sorted(positions) and len(set(positions)) == len(positions)
