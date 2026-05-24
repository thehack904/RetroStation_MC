# Installation

RetroStation MC v1.0.0 can run either as a Docker container or as a local Python application.

## Requirements

### Docker path

- Docker Engine or compatible container runtime
- Docker Compose v2
- Network access from IPTV clients to the app host and port

### Local Python path

- Python 3.11 or newer
- FFmpeg available in `PATH`
- Python packages from `requirements.txt`

Runtime Python dependencies:

```text
Flask==3.0.3
Pillow==10.4.0
python-dateutil==2.9.0.post0
```

## Docker Compose installation

From the repository root:

```bash
docker compose up --build
```

The included compose file maps the container port to host port `8787` and persists the main writable directories:

```yaml
services:
  retro-guide-poc:
    build: .
    container_name: retro-guide-poc
    ports:
      - "8787:8787"
    volumes:
      - ./data:/app/data
      - ./output:/app/output
      - ./sample_data:/app/sample_data
    environment:
      - RETROGUIDE_HOST=0.0.0.0
      - RETROGUIDE_PORT=8787
```

Open the admin UI:

```text
http://localhost:8787/
```

## Local Python installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app binds to `0.0.0.0:8787` by default.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RETROGUIDE_HOST` | `0.0.0.0` | Flask bind address |
| `RETROGUIDE_PORT` | `8787` | Flask port |
| `RETRO_TELEMETRY_DEBUG` | disabled | Enables low-frequency structured renderer/HLS telemetry logs when set to `1`, `true`, `yes`, or `on` |

Example:

```bash
RETROGUIDE_HOST=127.0.0.1 RETROGUIDE_PORT=8787 python app.py
```

## Persistent storage

Persist these directories when containerized:

| Directory | Required | Contents |
|---|---:|---|
| `data/` | Yes | SQLite config, events, guide state, uploaded music, PID files |
| `output/` | Recommended | Current HLS playlists and segments |
| `sample_data/` | Optional | Example inputs; useful for first-run validation |

## First-run behavior

On startup, RetroStation MC:

1. Creates `data/config.db` if it does not exist.
2. Inserts default settings when the database is empty.
3. Builds `data/guide_state.json` from configured M3U/XMLTV data.
4. Generates `output/standby.ts`.
5. Shows the standby stream until the admin clicks **Save & Start**.

The guide pipeline does not auto-start on a clean first run.

## Validation commands

Check the admin UI:

```bash
curl -I http://localhost:8787/
```

Check the single-channel playlist:

```bash
curl http://localhost:8787/channel.m3u
```

Check the HLS master playlist:

```bash
curl http://localhost:8787/hls/master.m3u8
```

Check status JSON:

```bash
curl http://localhost:8787/status
```
