"""SQLAlchemy models. Table/column names follow docs/schema.md (the team's data contract)."""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects import mysql

db = SQLAlchemy()

# MySQL DATETIME drops fractional seconds by default; keep microseconds so
# two scan cycles at the same spot within one second stay distinct.
TS = db.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def utcnow() -> datetime:
    """Naive UTC 'now' (the DB stores naive UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    return dt.isoformat() + "Z" if dt else None


class ScanSession(db.Model):
    __tablename__ = "scan_sessions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="active")  # active | stopped
    created_at = db.Column(TS, nullable=False, default=utcnow)
    stopped_at = db.Column(TS)

    blueprint_file = db.Column(db.String(255))
    blueprint_width = db.Column(db.Integer)
    blueprint_height = db.Column(db.Integer)

    # Live "you are here" position set by clicking on the blueprint.
    current_x = db.Column(db.Float)
    current_y = db.Column(db.Float)
    position_seq = db.Column(db.Integer, nullable=False, default=0)  # +1 on every click
    position_at = db.Column(TS)

    measurements = db.relationship(
        "Measurement", backref="session", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def has_blueprint(self):
        return bool(self.blueprint_file)

    def to_dict(self, measurement_count=None):
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "created_at": iso(self.created_at),
            "stopped_at": iso(self.stopped_at),
            "has_blueprint": self.has_blueprint,
            "blueprint_width": self.blueprint_width,
            "blueprint_height": self.blueprint_height,
            "measurement_count": measurement_count,
        }


class Measurement(db.Model):
    __tablename__ = "measurements"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey("scan_sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ssid = db.Column(db.String(64), index=True)       # NULL when no_signal
    bssid = db.Column(db.String(17))                  # AP MAC, aa:bb:cc:dd:ee:ff
    rssi = db.Column(db.Float)                        # dBm, NULL when no_signal
    latency = db.Column(db.Float)                     # ms, NULL = no reply / not measured
    packet_loss = db.Column(db.Float)                 # percent 0-100
    timestamp = db.Column(TS, nullable=False, index=True)
    x = db.Column(db.Float, nullable=False)           # blueprint pixels (stored image)
    y = db.Column(db.Float, nullable=False)
    no_signal = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "session_id": self.session_id,
            "ssid": self.ssid,
            "bssid": self.bssid,
            "rssi": self.rssi,
            "latency": self.latency,
            "packet_loss": self.packet_loss,
            "timestamp": iso(self.timestamp),
            "x": self.x,
            "y": self.y,
            "no_signal": self.no_signal,
        }
