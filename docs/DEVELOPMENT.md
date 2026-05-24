# Development Guide

## Repository layout

```text
.
├── app.py
├── app/
│   ├── config_store.py
│   ├── guide_state.py
│   ├── hls_playlist.py
│   ├── logging_utils.py
│   ├── m3u_parser.py
│   ├── manager.py
│   ├── renderer.py
│   ├── static/style.css
│   ├── templates/index.html
│   └── themes/*/theme.json
├── sample_data/
├── tests/
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
python app.py
```

Run tests:

```bash
pytest
```

## Important contracts

### Renderer contract

```text
guide_state.json → raw RGB stdout
```

FFmpeg depends on the renderer emitting continuous `rgb24` frames at the configured resolution and FPS.

### HLS public contract

The stable public stream path is:

```text
/hls/master.m3u8
```

The stable import playlist is:

```text
/channel.m3u
```

Avoid breaking these paths without a compatibility shim.

### Config contract

Settings are persisted as JSON values in SQLite. Add new settings to `DEFAULT_CONFIG` so fresh installs and existing installs receive merged defaults.

## Safe areas for extension

| Area | Extension examples |
|---|---|
| Themes | New `app/themes/<name>/theme.json` directories |
| Renderer | Better layout, logo usage, animation, SDL replacement |
| Admin UI | More controls, richer diagnostics, auth status |
| HLS | Multiple bitrate variants, better client fallback logic |
| Music | Volume controls, normalization, playlist preview |
| Input parsers | Better XMLTV timezone handling, gzip input, validation |

## Higher-risk areas

| Area | Risk |
|---|---|
| FFmpeg command construction | Small changes can break IPTV playback |
| Media sequence/start number | Resetting sequence numbers can stall clients after restart |
| Standby/live switching | Incorrect 404/cache behavior can trap clients on standby |
| PID reattach logic | Incorrect PID reuse handling can kill unrelated processes |
| Renderer stdout pacing | Blocking stdout stalls the pipeline |

## Adding a config setting

1. Add the key to `DEFAULT_CONFIG`.
2. Add form controls in `app/templates/index.html` if user-facing.
3. Update `coerce_form()` or the relevant settings route.
4. Decide whether the setting requires pipeline restart.
5. Update `docs/CONFIGURATION.md`.
6. Add or update tests.

## Adding a route

1. Add route in `app.py`.
2. Keep write routes local/admin-oriented unless authentication is added.
3. Set explicit response MIME type when serving media or generated files.
4. Update `docs/API_REFERENCE.md`.

## Replacing the renderer

A replacement renderer should preserve:

- input: `data/guide_state.json`
- output: raw frames to stdout or a clearly defined alternate pipe
- timing: continuous frame production at configured FPS
- resolution: exact configured width and height
- process lifecycle: start/stop under `GuideManager`

If the replacement does not output raw RGB frames, update the FFmpeg input arguments and test HLS playback thoroughly.
