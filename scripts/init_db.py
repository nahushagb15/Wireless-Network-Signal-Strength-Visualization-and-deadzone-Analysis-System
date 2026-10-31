"""Create the tables (and, for MySQL, the database itself if you have permission).

    python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.app import create_app
from backend.models import db
from config import Config


def ensure_mysql_database(url_str):
    url = make_url(url_str)
    if not url.drivername.startswith("mysql"):
        return
    server_url = url.set(database=None)
    engine = create_engine(server_url)
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    engine.dispose()


if __name__ == "__main__":
    ensure_mysql_database(Config.SQLALCHEMY_DATABASE_URI)
    app = create_app()
    with app.app_context():
        db.create_all()
        print("Tables ready:", ", ".join(db.metadata.tables))
        print("Database:", Config.SQLALCHEMY_DATABASE_URI.split("@")[-1])
