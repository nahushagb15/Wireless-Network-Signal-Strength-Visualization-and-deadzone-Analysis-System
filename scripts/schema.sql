-- Reference MySQL schema (SQLAlchemy creates this automatically; use this if you prefer manual setup).
CREATE DATABASE IF NOT EXISTS wifi_deadzone CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE wifi_deadzone;

CREATE TABLE IF NOT EXISTS scan_sessions (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  name             VARCHAR(120) NOT NULL,
  status           VARCHAR(16)  NOT NULL DEFAULT 'active',   -- active | stopped
  created_at       DATETIME(6)  NOT NULL,
  stopped_at       DATETIME(6)  NULL,
  blueprint_file   VARCHAR(255) NULL,
  blueprint_width  INT NULL,
  blueprint_height INT NULL,
  current_x        DOUBLE NULL,
  current_y        DOUBLE NULL,
  position_seq     INT NOT NULL DEFAULT 0,
  position_at      DATETIME(6) NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS measurements (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  session_id  INT NOT NULL,
  ssid        VARCHAR(64) NULL,
  bssid       VARCHAR(17) NULL,
  rssi        DOUBLE NULL,         -- dBm
  latency     DOUBLE NULL,         -- ms
  packet_loss DOUBLE NULL,         -- percent
  timestamp   DATETIME(6) NOT NULL,
  x           DOUBLE NOT NULL,
  y           DOUBLE NOT NULL,
  no_signal   TINYINT(1) NOT NULL DEFAULT 0,
  INDEX ix_measurements_session (session_id),
  INDEX ix_measurements_ssid (ssid),
  INDEX ix_measurements_timestamp (timestamp),
  CONSTRAINT fk_meas_session FOREIGN KEY (session_id)
    REFERENCES scan_sessions(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Handy check after a test walk:
-- SELECT x, y, ssid, bssid, rssi, latency, packet_loss, timestamp
-- FROM measurements WHERE session_id = 1 ORDER BY timestamp DESC LIMIT 20;
