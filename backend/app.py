"""Flask application: REST API + serves the single-page frontend."""
from __future__ import annotations

import csv
import io
import socket
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Flask, Response, jsonify, render_template, request, send_file
from sqlalchemy import func

from config import BASE_DIR, Config
from .analysis import METRICS, Thresholds, run_analysis
from .heatmap import prepare_blueprint, render_png
from .models import Measurement, ScanSession, db, iso, utcnow

api = Blueprint("api", __name__, url_prefix="/api")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


# ----------------------------------------------------------------- app factory
def create_app(config_object=Config):
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "frontend" / "templates"),
        static_folder=str(BASE_DIR / "frontend" / "static"),
    )
    app.config.from_object(config_object)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    with app.app_context():
        db.create_all()

    app.register_blueprint(api)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.errorhandler(ApiError)
    def _api_error(e):
        return jsonify(error=e.message), e.status

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        return e

    @app.errorhandler(413)
    def _413(e):
        return jsonify(error="File too large (limit 16 MB)."), 413

    return app


# --------------------------------------------------------------------- helpers
def get_session_or_404(session_id) -> ScanSession:
    s = db.session.get(ScanSession, session_id)
    if s is None:
        raise ApiError(f"Session {session_id} does not exist.", 404)
    return s


def require_active(s: ScanSession):
    if s.status != "active":
        raise ApiError(f"Session '{s.name}' is stopped. Resume it to record more data.", 409)


def _cfg(key):
    from flask import current_app
    return current_app.config[key]


def blueprint_path(s: ScanSession) -> Path:
    return Path(_cfg("UPLOAD_DIR")) / s.blueprint_file


def num(value, name, lo=None, hi=None, required=False):
    if value is None or value == "":
        if required:
            raise ApiError(f"'{name}' is required.")
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ApiError(f"'{name}' must be a number.")
    if v != v or v in (float("inf"), float("-inf")):
        raise ApiError(f"'{name}' must be a finite number.")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ApiError(f"'{name}' must be between {lo} and {hi}.")
    return v


def parse_timestamp(value):
    if not value:
        return utcnow()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("'timestamp' must be ISO-8601, e.g. 2026-10-31T10:15:00Z.")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def thresholds_from_args(args) -> Thresholds:
    d = Thresholds()
    return Thresholds(
        rssi=num(args.get("rssi_thr"), "rssi_thr", -120, 0) if args.get("rssi_thr") else d.rssi,
        strong_rssi=d.strong_rssi,
        packet_loss=num(args.get("loss_thr"), "loss_thr", 0, 100) if args.get("loss_thr") else d.packet_loss,
        latency=num(args.get("latency_thr"), "latency_thr", 1, 10000) if args.get("latency_thr") else d.latency,
        min_readings=int(num(args.get("min_readings"), "min_readings", 1, 100)) if args.get("min_readings") else d.min_readings,
        weak_fraction=d.weak_fraction,
    )


def load_rows(session_id):
    q = db.session.query(
        Measurement.x, Measurement.y, Measurement.timestamp, Measurement.ssid,
        Measurement.bssid, Measurement.rssi, Measurement.latency,
        Measurement.packet_loss, Measurement.no_signal,
    ).filter(Measurement.session_id == session_id)
    return [dict(zip(("x", "y", "timestamp", "ssid", "bssid", "rssi", "latency",
                      "packet_loss", "no_signal"), r)) for r in q]


def analysis_for(s: ScanSession):
    if not s.has_blueprint:
        raise ApiError("Upload a blueprint for this session first.", 409)
    metric = request.args.get("metric", "rssi")
    if metric not in METRICS:
        raise ApiError(f"'metric' must be one of {', '.join(METRICS)}.")
    th = thresholds_from_args(request.args)
    ssid = request.args.get("ssid") or None
    bssid = request.args.get("bssid") or None
    result = run_analysis(load_rows(s.id), s.blueprint_width, s.blueprint_height,
                          th, ssid, bssid, metric)
    return result


# -------------------------------------------------------------------- sessions
@api.get("/health")
def health():
    return jsonify(status="ok", time=iso(utcnow()))


@api.get("/network")
def network_info():
    """The laptop's LAN address, so the UI can show a QR code for the phone."""
    ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no packet is sent; just picks the Wi-Fi interface
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = None
    if ip and ip.startswith("127."):
        ip = None
    port = request.environ.get("SERVER_PORT", "5000")
    return jsonify(ip=ip, url=f"http://{ip}:{port}" if ip else None)


@api.post("/sessions")
def start_session():
    """start_session: create and activate a new scan session."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        raise ApiError("Give the session a name.")
    if len(name) > 120:
        raise ApiError("Session name is too long (max 120 characters).")
    s = ScanSession(name=name)
    db.session.add(s)
    db.session.commit()
    return jsonify(s.to_dict(0)), 201


@api.get("/sessions")
def list_sessions():
    counts = dict(db.session.query(Measurement.session_id, func.count(Measurement.id))
                  .group_by(Measurement.session_id).all())
    rows = ScanSession.query.order_by(ScanSession.created_at.desc()).all()
    return jsonify([s.to_dict(counts.get(s.id, 0)) for s in rows])


@api.get("/sessions/<int:sid>")
def get_session(sid):
    s = get_session_or_404(sid)
    count = db.session.query(func.count(Measurement.id)).filter_by(session_id=sid).scalar()
    return jsonify(s.to_dict(count))


@api.post("/sessions/<int:sid>/stop")
def stop_session(sid):
    s = get_session_or_404(sid)
    if s.status == "active":
        s.status, s.stopped_at = "stopped", utcnow()
        db.session.commit()
    return jsonify(s.to_dict())


@api.post("/sessions/<int:sid>/resume")
def resume_session(sid):
    s = get_session_or_404(sid)
    s.status, s.stopped_at = "active", None
    db.session.commit()
    return jsonify(s.to_dict())


@api.delete("/sessions/<int:sid>")
def delete_session(sid):
    s = get_session_or_404(sid)
    if s.blueprint_file:
        try:
            blueprint_path(s).unlink(missing_ok=True)
        except OSError:
            pass
    Measurement.query.filter_by(session_id=sid).delete()
    db.session.delete(s)
    db.session.commit()
    return jsonify(deleted=sid)


# ------------------------------------------------------------------- blueprint
@api.post("/sessions/<int:sid>/blueprint")
def upload_blueprint(sid):
    """upload_blueprint: accept an image, normalise it with Pillow, store it."""
    s = get_session_or_404(sid)
    f = request.files.get("file")
    if f is None or not f.filename:
        raise ApiError("Choose an image file to upload (form field 'file').")
    has_data = db.session.query(func.count(Measurement.id)).filter_by(session_id=sid).scalar()
    if has_data:
        raise ApiError("This session already has measurements. Start a new session to use a different blueprint.", 409)
    try:
        img = prepare_blueprint(f.stream, _cfg("MAX_BLUEPRINT_PX"))
    except ValueError as e:
        raise ApiError(str(e), 400)
    filename = f"session_{sid}.png"
    img.save(Path(_cfg("UPLOAD_DIR")) / filename, format="PNG")
    s.blueprint_file, s.blueprint_width, s.blueprint_height = filename, img.width, img.height
    s.current_x = s.current_y = None
    db.session.commit()
    return jsonify(s.to_dict())


@api.get("/sessions/<int:sid>/blueprint")
def get_blueprint(sid):
    s = get_session_or_404(sid)
    if not s.has_blueprint:
        raise ApiError("No blueprint uploaded yet.", 404)
    return send_file(blueprint_path(s), mimetype="image/png", max_age=0)


# -------------------------------------------------------------------- position
@api.post("/sessions/<int:sid>/position")
def set_position(sid):
    """Called by the frontend when the user clicks the blueprint."""
    s = get_session_or_404(sid)
    require_active(s)
    if not s.has_blueprint:
        raise ApiError("Upload a blueprint before marking positions.", 409)
    data = request.get_json(silent=True) or {}
    x = num(data.get("x"), "x", 0, s.blueprint_width, required=True)
    y = num(data.get("y"), "y", 0, s.blueprint_height, required=True)
    s.current_x, s.current_y = round(x, 1), round(y, 1)
    s.position_seq += 1
    s.position_at = utcnow()
    db.session.commit()
    return jsonify(x=s.current_x, y=s.current_y, seq=s.position_seq)


@api.get("/sessions/<int:sid>/position")
def get_position(sid):
    """Polled by the scanner to learn where the walker currently stands."""
    s = get_session_or_404(sid)
    return jsonify(x=s.current_x, y=s.current_y, seq=s.position_seq,
                   updated_at=iso(s.position_at), status=s.status)


# ---------------------------------------------------------------- measurements
@api.post("/measurements")
def submit_measurement():
    """submit_measurement: store one scan cycle (one or many BSSID readings).

    Body: {session_id, x, y, timestamp?, measurements:[{ssid,bssid,rssi,latency,
    packet_loss,no_signal}]}.  A flat single reading is accepted too.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Send a JSON object.")
    sid = data.get("session_id")
    if sid is None:
        raise ApiError("'session_id' is required. Start a session first.")
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        raise ApiError("'session_id' must be a number.")
    s = get_session_or_404(sid)
    require_active(s)                      # blocks submissions without an active session
    if not s.has_blueprint:
        raise ApiError("Upload a blueprint for this session first.", 409)

    x = num(data.get("x"), "x", 0, s.blueprint_width, required=True)
    y = num(data.get("y"), "y", 0, s.blueprint_height, required=True)
    ts = parse_timestamp(data.get("timestamp"))
    readings = data.get("measurements")
    if readings is None:
        readings = [data]
    if not isinstance(readings, list) or not readings:
        raise ApiError("'measurements' must be a non-empty list.")
    max_readings = _cfg("MAX_READINGS_PER_REQUEST")
    if len(readings) > max_readings:
        raise ApiError(f"Too many readings in one request (max {max_readings}).")

    objs = []
    for i, r in enumerate(readings):
        if not isinstance(r, dict):
            raise ApiError(f"measurements[{i}] must be an object.")
        no_signal = bool(r.get("no_signal"))
        rssi = num(r.get("rssi"), "rssi", -120, 0, required=not no_signal)
        ssid = None if no_signal else (str(r.get("ssid") or "<hidden>")[:64])
        bssid = None if no_signal else (str(r["bssid"]).lower()[:17] if r.get("bssid") else None)
        objs.append(Measurement(
            session_id=s.id, ssid=ssid, bssid=bssid,
            rssi=None if no_signal else rssi,
            latency=num(r.get("latency"), "latency", 0, 600000),
            packet_loss=num(r.get("packet_loss"), "packet_loss", 0, 100),
            timestamp=ts, x=round(x, 1), y=round(y, 1), no_signal=no_signal,
        ))
    db.session.add_all(objs)
    db.session.commit()
    return jsonify(stored=len(objs), session_id=s.id), 201


@api.get("/sessions/<int:sid>/data")
def get_session_data(sid):
    """get_session_data: raw rows (newest first), optional ?ssid=&limit=."""
    s = get_session_or_404(sid)
    limit = int(num(request.args.get("limit", 200), "limit", 1, 5000))
    q = Measurement.query.filter_by(session_id=sid)
    if request.args.get("ssid"):
        q = q.filter(Measurement.ssid == request.args["ssid"])
    total = q.count()
    rows = q.order_by(Measurement.timestamp.desc(), Measurement.id.desc()).limit(limit).all()
    return jsonify(session=s.to_dict(total), total=total, rows=[m.to_dict() for m in rows])


@api.get("/sessions/<int:sid>/ssids")
def list_ssids(sid):
    get_session_or_404(sid)
    q = (db.session.query(Measurement.ssid, func.count(func.distinct(Measurement.bssid)),
                          func.count(Measurement.id))
         .filter(Measurement.session_id == sid, Measurement.ssid.isnot(None))
         .group_by(Measurement.ssid).order_by(func.count(Measurement.id).desc()))
    return jsonify([{"ssid": a, "access_points": b, "readings": c} for a, b, c in q])


# -------------------------------------------------------------------- analysis
@api.get("/sessions/<int:sid>/analysis")
def get_analysis(sid):
    s = get_session_or_404(sid)
    return jsonify(analysis_for(s))


@api.get("/sessions/<int:sid>/heatmap.png")
def heatmap_png(sid):
    """Flattened export: blueprint + heatmap + deadzone circles (for the report)."""
    s = get_session_or_404(sid)
    result = analysis_for(s)
    png = render_png(blueprint_path(s), result)
    resp = Response(png, mimetype="image/png")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f'attachment; filename="heatmap_session_{sid}.png"'
    return resp


@api.get("/sessions/<int:sid>/export.csv")
def export_csv(sid):
    s = get_session_or_404(sid)
    cols = ["timestamp", "x", "y", "ssid", "bssid", "rssi", "latency", "packet_loss", "no_signal"]

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        yield buf.getvalue()
        q = Measurement.query.filter_by(session_id=sid).order_by(Measurement.timestamp, Measurement.id)
        for m in q.yield_per(500):
            buf.seek(0); buf.truncate()
            d = m.to_dict()
            w.writerow([d[c] for c in cols])
            yield buf.getvalue()

    return Response(generate(), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="session_{sid}_{s.name[:30]}.csv"'.replace(" ", "_")})
