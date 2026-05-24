# Architecture

RetroStation MC v1.0.0 is a small Flask application with a managed renderer/encoder pipeline.

## Component overview

```text
Browser Admin UI
      │
      ▼
Flask app.py
      │
      ├── ConfigStore ───────► data/config.db
      │
      ├── GuideManager
      │       │
      │       ├── M3U parser
      │       ├── XMLTV parser
      │       ├── guide_state builder ───► data/guide_state.json
      │       ├── renderer.py process ───► raw RGB stdout
      │       └── ffmpeg process ────────► output/guide.m3u8 + output/guide_*.ts
      │
      ├── HLS endpoints
      │       ├── /hls/master.m3u8
      │       ├── /hls/standby.m3u8
      │       ├── /hls/live.m3u8
      │       └── /hls/<segment>.ts
      │
      └── Integration endpoints
              ├── /channel.m3u
              ├── /channel.m3u8
              └── /channel.xmltv
```

## Main files

| File | Purpose |
|---|---|
| `app.py` | Flask entrypoint, routes, admin UI wiring, HLS serving, music endpoints |
| `app/config_store.py` | SQLite-backed configuration and app event storage |
| `app/manager.py` | Worker loop, renderer/FFmpeg lifecycle, standby generation, audio selection |
| `app/guide_state.py` | Converts config + M3U + XMLTV into normalized renderer state |
| `app/renderer.py` | Pillow-based frame renderer that writes raw RGB frames to stdout |
| `app/hls_playlist.py` | Live-edge trimming helper for HLS playlists |
| `app/m3u_parser.py` | Basic M3U parser |
| `app/xmltv_parser.py` | Basic XMLTV parser |
| `app/logging_utils.py` | Event logger wrapper |
| `app/templates/index.html` | Single-page admin dashboard |
| `app/static/style.css` | Admin dashboard styling |
| `app/themes/*/theme.json` | Theme definitions |

## Process model

The Flask process starts a `GuideManager`. The manager runs a background worker thread that periodically refreshes guide state and checks whether an intentionally active pipeline is still running.

When the admin starts the guide, the manager launches two subprocesses:

1. `renderer.py` emits raw RGB video frames to stdout.
2. `ffmpeg` reads those frames from stdin, adds audio, encodes H.264/AAC, and writes HLS media.

The pipeline is intentionally separate from Flask. PID files allow a new Flask process to reattach to still-running renderer/FFmpeg processes after a Flask restart.

## First-run standby behavior

On clean startup with no surviving pipeline, the app:

1. Refreshes guide state.
2. Generates `output/standby.ts`.
3. Leaves the live pipeline stopped.
4. Serves standby playlists until the admin explicitly starts the guide.

This prevents the app from auto-starting FFmpeg before configuration has been reviewed.

## Reattach behavior

The manager writes:

- `data/renderer.pid`
- `data/ffmpeg.pid`

On startup, it checks whether those PIDs are alive and whether their command lines match expected fragments. If both are valid, it marks the pipeline active instead of killing and respawning it.

## Data storage

SQLite stores two tables:

| Table | Purpose |
|---|---|
| `settings` | JSON-encoded key/value config |
| `app_events` | timestamped event log rows |

The renderer state is a JSON file, not stored in SQLite:

```text
data/guide_state.json
```

This keeps the renderer process simple and decoupled from the Flask app.

## Design intent

The Python/Pillow renderer is deliberately replaceable. The architecture separates:

- admin/configuration
- guide state generation
- rendering
- HLS packaging
- IPTV integration endpoints

A future SDL/C/Rust renderer should be able to consume the same `guide_state.json` contract while leaving the Flask admin and HLS serving model largely intact.
