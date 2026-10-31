"""API tests (need Flask-SQLAlchemy installed: pip install -r requirements.txt, then pytest)."""
import io

import pytest
from PIL import Image

from backend.app import create_app
from config import TestConfig


@pytest.fixture()
def client(tmp_path):
    class Cfg(TestConfig):
        UPLOAD_DIR = tmp_path

    app = create_app(Cfg)
    with app.test_client() as c:
        yield c


def png_bytes(w=400, h=300):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    buf.seek(0)
    return buf


def make_session(client, blueprint=True):
    sid = client.post("/api/sessions", json={"name": "lab"}).get_json()["id"]
    if blueprint:
        r = client.post(f"/api/sessions/{sid}/blueprint",
                        data={"file": (png_bytes(), "plan.png")}, content_type="multipart/form-data")
        assert r.status_code == 200
    return sid


def reading(sid, x, y, rssi=-50, ts="2026-10-31T10:00:00Z", **extra):
    return {"session_id": sid, "x": x, "y": y, "timestamp": ts, "measurements": [
        {"ssid": "Net", "bssid": "AA:BB:CC:DD:EE:01", "rssi": rssi, **extra}]}


def test_session_needs_name(client):
    assert client.post("/api/sessions", json={"name": " "}).status_code == 400


def test_measurement_blocked_without_session(client):
    r = client.post("/api/measurements", json={"x": 1, "y": 1, "rssi": -50})
    assert r.status_code == 400
    r = client.post("/api/measurements", json=reading(999, 1, 1))
    assert r.status_code == 404


def test_measurement_blocked_when_stopped(client):
    sid = make_session(client)
    client.post(f"/api/sessions/{sid}/stop")
    assert client.post("/api/measurements", json=reading(sid, 10, 10)).status_code == 409
    client.post(f"/api/sessions/{sid}/resume")
    assert client.post("/api/measurements", json=reading(sid, 10, 10)).status_code == 201


def test_validation(client):
    sid = make_session(client)
    assert client.post("/api/measurements", json=reading(sid, 9999, 10)).status_code == 400   # off the plan
    assert client.post("/api/measurements", json=reading(sid, 10, 10, rssi=5)).status_code == 400
    assert client.post("/api/measurements", json=reading(sid, 10, 10, packet_loss=150)).status_code == 400


def test_bad_image_rejected(client):
    sid = make_session(client, blueprint=False)
    r = client.post(f"/api/sessions/{sid}/blueprint",
                    data={"file": (io.BytesIO(b"not an image"), "x.png")}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_position_roundtrip_and_scanner_flow(client):
    sid = make_session(client)
    assert client.get(f"/api/sessions/{sid}/position").get_json()["x"] is None
    r = client.post(f"/api/sessions/{sid}/position", json={"x": 120, "y": 80})
    assert r.get_json()["seq"] == 1
    pos = client.get(f"/api/sessions/{sid}/position").get_json()
    assert (pos["x"], pos["y"]) == (120, 80)
    assert client.post(f"/api/sessions/{sid}/position", json={"x": -5, "y": 1}).status_code == 400


def test_end_to_end_deadzone(client):
    sid = make_session(client)
    for t in range(3):   # weak spot, three cycles, plus no-signal handling
        ts = f"2026-10-31T10:00:0{t}Z"
        assert client.post("/api/measurements", json=reading(sid, 300, 200, -88, ts)).status_code == 201
        assert client.post("/api/measurements", json=reading(sid, 50, 50, -45, ts)).status_code == 201
    assert client.post("/api/measurements", json={
        "session_id": sid, "x": 200, "y": 100,
        "measurements": [{"no_signal": True, "packet_loss": 100}]}).status_code == 201

    a = client.get(f"/api/sessions/{sid}/analysis").get_json()
    assert a["summary"]["deadzone"] == 1
    assert a["regions"] and a["regions"][0]["id"] == 1
    assert client.get(f"/api/sessions/{sid}/heatmap.png").mimetype == "image/png"
    assert client.get(f"/api/sessions/{sid}/export.csv").status_code == 200
    assert client.get(f"/api/sessions/{sid}/ssids").get_json()[0]["ssid"] == "Net"
    assert client.get(f"/api/sessions/{sid}/analysis?metric=bogus").status_code == 400


def test_delete_session(client):
    sid = make_session(client)
    client.post("/api/measurements", json=reading(sid, 10, 10))
    assert client.delete(f"/api/sessions/{sid}").status_code == 200
    assert client.get(f"/api/sessions/{sid}").status_code == 404
