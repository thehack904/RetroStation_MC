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

To prevent unbounded resource growth, event storage is trimmed automatically when
`data/config.db` grows beyond 500 MB. The oldest events are dropped first.

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


## Guide Preview diagnostics

Guide Preview startup writes transport/pipeline information to the event log. Useful categories/messages include `config` entries showing the cached detected transport, `guide-preview-normalizer` for the shared HLS/local-file worker, and `guide-preview-mpegts-relay` / MPEG-TS relay startup messages.

For Preview A/V or startup problems, confirm that the selected source's cached transport matches the current source, then look for normalizer/relay startup failures or the eight-second first-output startup timeout. An unknown network transport is not automatically treated as MPEG-TS; it follows the conservative shared-normalizer path.
## Guide Preview audio sync compensation

The Diagnostics tab includes source-specific **Guide Preview Audio Sync Compensation** controls for:

- **HDHomeRun Audio Offset**
- **IPTV / M3U Audio Offset**
- **Uploaded Video Audio Offset**

Each control ranges from `-5000` to `+5000` milliseconds in 50 ms steps and displays the equivalent seconds value live. For example, `-1200 ms` is displayed as `-1200 ms (-1.20 seconds)`.

The sign convention is:

- negative = advance audio
- positive = delay audio
- zero = no compensation

These settings apply only when Guide Preview audio mode is **Preview** and the selected source class matches the control. HDHomeRun and IPTV/M3U are independent. The controls do not alter Preview video timestamps or cadence.

After changing an offset on a running Guide, use **Save & Restart Pipeline** so the final Guide FFmpeg command is rebuilt. To verify the effective setting at the command line, inspect the active Guide process. A negative offset uses an audio filter similar to `asetpts=PTS-STARTPTS,atrim=start=1.2,asetpts=PTS-STARTPTS`; a positive offset uses `adelay=<milliseconds>:all=1`.

Do not assume a compensation value is portable between systems. Source encoder latency, hardware acceleration, host performance, and playback paths can differ. Start at zero and calibrate only when a consistent A/V offset is observed.

