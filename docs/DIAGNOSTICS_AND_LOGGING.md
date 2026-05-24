# Diagnostics and Logging

RetroStation MC stores application events in SQLite and exposes HLS tuning controls through the admin UI.

## Event storage

Events are stored in:

```text
data/config.db
```

Table:

```text
app_events(created_at, level, category, message)
```

The admin UI shows the most recent events based on `diag_log_tail_lines`.

## Logs UI

The Logs tab provides:

- recent event display
- total stored event count
- JSONL export
- CSV export
- direct link to `/logs?limit=500&offset=0`

## Logs API

```bash
curl 'http://localhost:8787/logs?limit=200&offset=0'
```

Response shape:

```json
{
  "total": 123,
  "count": 200,
  "offset": 0,
  "limit": 200,
  "events": []
}
```

## Export logs

JSONL:

```bash
curl -o retrostation-events.jsonl 'http://localhost:8787/logs/export?format=jsonl'
```

CSV:

```bash
curl -o retrostation-events.csv 'http://localhost:8787/logs/export?format=csv'
```

## Diagnostics settings

| Setting | Default | Range | Purpose |
|---|---:|---:|---|
| Delay Segments | `2` | 1-120 | Hide newest live segments from clients |
| Minimum Buffer Seconds | `18` | 1-900 | Minimum wall-clock age before live switch after fresh start |
| Minimum Buffer Segments | `3` | 1-300 | Required live segment count before live switch |
| Standby Window Segments | `3` | 1-20 | Synthetic standby playlist entry count |
| Log Tail Lines | `120` | 10-2000 | Recent logs shown in UI |

## Telemetry debug mode

Set this environment variable before starting the app:

```bash
RETRO_TELEMETRY_DEBUG=1
```

When enabled, the manager starts renderer telemetry and HLS monitor logging. Events are emitted at a throttled cadence.

Telemetry categories include:

- `renderer.telemetry`
- `ffmpeg.telemetry`
- `hls.telemetry`

The telemetry is meant for diagnosing timing instability, stdout blocking, segment cadence variance, playlist update cadence, and frame/render drift.

## When to adjust diagnostics

Increase buffer settings when clients show:

- spinner loops after starting the guide
- playback catching up to the newest segment and stalling
- repeated standby/live transition instability

Decrease buffer settings only when startup latency is more important than playback stability.
