# API Reference

RetroStation MC exposes a small set of Flask routes. Most write routes are intended for the admin UI, not for public API consumption.

## Admin pages and actions

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Admin dashboard |
| `POST` | `/config` | Save main configuration; optionally start/restart guide |
| `POST` | `/refresh` | Rebuild guide state from M3U/XMLTV |
| `POST` | `/stop` | Stop renderer/FFmpeg and return to standby |
| `POST` | `/restart` | Refresh state and restart renderer/FFmpeg |
| `GET` | `/status` | Return JSON pipeline status |

## Diagnostics and logs

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/diagnostics/settings` | Save HLS diagnostics/buffer settings |
| `GET` | `/logs?limit=N&offset=N` | Return paginated JSON event log data |
| `GET` | `/logs/export?format=jsonl` | Download full logs as JSONL |
| `GET` | `/logs/export?format=csv` | Download full logs as CSV |

## IPTV integration endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/channel.m3u` | Single-channel M3U playlist, MIME `application/x-mpegURL` |
| `GET` | `/channel.m3u8` | Same single-channel playlist, MIME `application/vnd.apple.mpegurl` |
| `GET` | `/channel.xmltv` | XMLTV guide for the virtual guide channel |

## HLS endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/hls/master.m3u8` | HLS master playlist that selects standby or live media |
| `GET` | `/hls/standby.m3u8` | Synthetic standby media playlist; returns 404 once live is ready |
| `GET` | `/hls/live.m3u8` | Live guide media playlist; returns 404 until live is ready |
| `GET` | `/hls/guide.m3u8` | Backward-compatible unified media playlist |
| `GET` | `/hls/<filename>` | Static HLS playlist or segment file from `output/` |
| `OPTIONS` | `/hls/master.m3u8` | CORS preflight |
| `OPTIONS` | `/hls/<filename>` | CORS preflight |

HLS responses include permissive CORS headers for playback clients:

```text
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, OPTIONS
Access-Control-Allow-Headers: Range
```

## Music endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/music/upload` | Upload one or more audio files |
| `POST` | `/music/delete/<filename>` | Delete an uploaded music file |
| `POST` | `/music/settings` | Save music mode, loop setting, and file selection |

## `/status` response fields

Typical fields:

| Field | Meaning |
|---|---|
| `renderer_running` | Whether the renderer PID exists and is alive |
| `ffmpeg_running` | Whether the FFmpeg PID exists and is alive |
| `pipeline_active` | Whether the guide was intentionally started |
| `guide_buffered` | Whether live HLS output is ready for clients |
| `stream_version` | Monotonic counter incremented on standby/live transitions |
| `last_refresh_status` | Last state refresh result |
| `current_theme` | Active theme name |
| `playlist_source` | Configured M3U source |
| `xmltv_source` | Configured XMLTV source |
| `stream_url` | Public stream path, currently `/hls/master.m3u8` |
