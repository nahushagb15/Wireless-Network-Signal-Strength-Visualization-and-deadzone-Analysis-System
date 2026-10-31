# Wi-Fi Signal Strength Visualization and Deadzone Analysis System

Walk through a building with a laptop, click your position on the floor plan, and the system
records Wi-Fi signal strength (RSSI), latency and packet loss at each spot, then draws a
green / yellow / red heatmap over the floor plan and circles the deadzones.

```
 walking laptop                         server (Flask)                     any browser
┌──────────────────┐   POST readings   ┌───────────────────┐   REST/JSON   ┌────────────────────┐
│ scanner/         │ ────────────────► │ backend/app.py    │ ◄──────────── │ frontend/          │
│  netsh / nmcli   │                   │ SQLAlchemy ──► MySQL               │ upload blueprint   │
│  ping3           │ ◄──────────────── │ analysis.py       │ ────────────► │ click to mark spot │
│                  │  GET position     │ heatmap.py (Pillow)│  heatmap data │ Plotly heatmap     │
└──────────────────┘                   └───────────────────┘               └────────────────────┘
```

The **click** sets "where the laptop is now". The scanner polls that position and tags every
reading with it. The clicker can be a second person on a phone, or you can pause at checkpoints.

## Quick start (5 minutes, no database setup)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
.venv\Scripts\python launch.py                    
```

Try it without any Wi-Fi hardware using the built-in simulator:

1. Open http://localhost:5000, create a session and upload any floor-plan image.
2. In a second terminal (same venv), using the session id shown in the UI:
   `python -m scanner.scanner --session 1 --simulate --auto-walk`
3. Watch the heatmap and deadzones appear. (Simulated data is for testing only. Your report needs real data.)

## Using real data

On the laptop that will walk around (Windows or Linux):

```bash
python -m scanner.scanner --api http://SERVER_IP:5000 --session 1 --ping-host 192.168.1.1
```

| Option | Meaning |
|---|---|
| `--mode continuous` (default) | measure every `--interval` seconds at the last clicked position |
| `--mode checkpoint` | measure only after each click, `--samples` cycles per click (best for pausing at spots) |
| `--ping-host` | your **router's IP** measures Wi-Fi quality only; the default `8.8.8.8` also includes your ISP |
| `--backend netsh|nmcli|pywifi` | force a scan backend (default picks by OS) |

**Walking method:** open the UI on a phone, tick *Marking position*, stand at a spot, tap it on the
plan, wait for 3 log lines (checkpoint mode) or ~10 s (continuous), walk to the next spot, repeat.
Cover the whole area, 15 to 30 spots is a good map. At least 3 readings per spot are required to
declare a deadzone.

### Platform notes
- **Windows:** uses `netsh wlan`. Windows 11 may require *Settings > Privacy & security > Location* to be on for the scan to return results. Windows can serve cached scan results; if the readings look frozen use `--backend pywifi` (`pip install pywifi comtypes`), which forces a fresh scan.
- **Linux:** uses `nmcli` (NetworkManager). `ping3` may need raw-socket permission; if not available the scanner automatically falls back to the system `ping`.
- **macOS:** no supported scan backend. Use it as the server/clicker only.
- netsh/nmcli report signal *quality* (0-100%); it is converted to dBm with `dBm = quality/2 - 100`. Values are an estimate, comparable between spots but not lab-grade.
- Latency / packet loss are measured on the **connected** network, so they are stored only on the connected AP's row. Choosing a different SSID in the UI shows signal strength only.

## Shared MySQL database (team setup)

```sql
CREATE DATABASE wifi_deadzone CHARACTER SET utf8mb4;
CREATE USER 'wifi'@'%' IDENTIFIED BY 'a-strong-password';
GRANT ALL ON wifi_deadzone.* TO 'wifi'@'%';
```
Copy `.env.example` to `.env` and set
`DATABASE_URL=mysql+pymysql://wifi:a-strong-password@HOST:3306/wifi_deadzone?charset=utf8mb4`,
then `python scripts/init_db.py` (or run `scripts/schema.sql` by hand). Without `DATABASE_URL`
the app uses a local SQLite file.

## How deadzones are detected (`backend/analysis.py`)

1. **One reading per scan cycle.** Several BSSIDs with the same SSID (multiple APs) collapse to the strongest, as a roaming client would. A network not heard in a cycle counts as -100 dBm; a cycle with no Wi-Fi at all is stored as `no_signal`.
2. **A cycle is weak** if RSSI < -75 dBm, or packet loss > 10 %, or latency > 150 ms (all editable in the UI).
3. **A spot is a deadzone** if it has at least 3 cycles and at least 60 % are weak ("repeated weak readings"). Fewer than 3 cycles gives *suspect*, which is shown in orange and not counted as a deadzone.
4. Neighbouring deadzone spots are merged into numbered **regions** (circles on the map).
5. **Heatmap:** inverse-distance-weighted interpolation between measured spots. Areas far from any measurement are left unpainted instead of guessed.

Colours: green is strong (at or above -60 dBm), yellow is moderate, red is deadzone (below -75 dBm).

## API

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/sessions` | start_session `{name}` |
| GET | `/api/sessions`, `/api/sessions/<id>` | list / details |
| POST | `/api/sessions/<id>/stop` or `/resume` | stop / resume scanning |
| DELETE | `/api/sessions/<id>` | delete session + readings |
| POST | `/api/sessions/<id>/blueprint` | upload_blueprint (multipart `file`) |
| GET | `/api/sessions/<id>/blueprint` | stored (normalised) image |
| POST / GET | `/api/sessions/<id>/position` | set by the UI click / polled by the scanner |
| POST | `/api/measurements` | submit_measurement (rejected unless the session is active) |
| GET | `/api/sessions/<id>/data` | get_session_data (raw rows, `?ssid=&limit=`) |
| GET | `/api/sessions/<id>/ssids` | networks seen |
| GET | `/api/sessions/<id>/analysis` | points, regions, grid, summary. Params: `ssid, metric, rssi_thr, loss_thr, latency_thr, min_readings` |
| GET | `/api/sessions/<id>/heatmap.png` | flattened export (Pillow) |
| GET | `/api/sessions/<id>/export.csv` | raw data |

Data contract: `docs/schema.md`.

## Tests

```bash
pip install -r requirements.txt
pytest -q
```
`test_analysis.py` (detection logic) and `test_scanner_parsers.py` (netsh/nmcli parsing) need only numpy. `test_api.py` drives every endpoint with an in-memory database.

## Project layout and roadmap mapping

| Path | Roadmap role |
|---|---|
| `scanner/` | Person 1: Data collection engine |
| `backend/`, `config.py`, `scripts/` | Person 2: Flask API, MySQL/SQLAlchemy, deadzone logic |
| `frontend/`, `backend/heatmap.py` | Person 3: Blueprint UI, click-to-mark, Plotly heatmap, Pillow overlay |
| `docs/schema.md`, `tests/` | Phase 0 data contract, Phase 4 validation |

## Known limitations
- The browser needs internet access for the Plotly script and Google Font (CDN). On an offline LAN, download `plotly-2.35.2.min.js` into `frontend/static/` and change the `<script>` tag.
- No login. Anyone who can reach the server can use it, so run it on a trusted network.
- Multi-floor support and historical trend comparison (the roadmap's stretch goals) are not implemented.
