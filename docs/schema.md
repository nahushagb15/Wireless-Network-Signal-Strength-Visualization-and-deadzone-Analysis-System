# Data contract

## measurements (one row per BSSID seen in one scan cycle)

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| session_id | int FK | `scan_sessions.id`, cascade delete |
| ssid | varchar(64) | `<hidden>` for hidden networks; NULL when `no_signal` |
| bssid | varchar(17) | lower-case MAC; NULL when `no_signal` |
| rssi | float | dBm, -120 to 0; NULL when `no_signal` |
| latency | float | ms; NULL = no reply or not attached to this AP |
| packet_loss | float | percent 0-100 |
| timestamp | datetime(6) | UTC. **All rows of one scan cycle share the same timestamp**, which is how the analysis groups them |
| x, y | float | pixels on the *stored* blueprint (top-left origin) |
| no_signal | bool | true when the cycle heard no Wi-Fi at all |

## scan_sessions

`id, name, status (active|stopped), created_at, stopped_at, blueprint_file, blueprint_width,
blueprint_height, current_x, current_y, position_seq, position_at`

`position_seq` increases on every click, so the scanner can detect a new click (checkpoint mode).

## POST /api/measurements

```json
{
  "session_id": 3, "x": 412.5, "y": 220.0, "timestamp": "2026-10-31T10:15:00+00:00",
  "measurements": [
    {"ssid": "CampusNet", "bssid": "aa:bb:cc:dd:ee:01", "rssi": -58, "latency": 14.2, "packet_loss": 0},
    {"ssid": "CampusNet", "bssid": "aa:bb:cc:dd:ee:02", "rssi": -71, "latency": null, "packet_loss": null}
  ]
}
```
No Wi-Fi heard: `"measurements": [{"no_signal": true, "packet_loss": 100}]`.
Coordinates must lie inside the blueprint, and the session must be active, otherwise the request is rejected (400/409).
Changing this contract means updating `scanner/scanner.py`, `backend/app.py` and this file together.
