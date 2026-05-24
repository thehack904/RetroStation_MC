# Admin User Guide

The admin UI is available at the root path:

```text
http://YOUR_SERVER:8787/
```

v1.0.0 has no login screen. Keep the app local-only or place it behind external authentication.

## Header controls

The top status bar shows:

- Running or stopped state
- Active theme
- Resolution and FPS
- Last refresh status
- Last logged event timestamp

Header actions:

| Action | Effect |
|---|---|
| **Restart Guide** | Rebuilds guide state and restarts renderer + FFmpeg |
| **Stop Guide** | Stops renderer + FFmpeg and returns the stream to standby mode |
| **Refresh EPG** | Rebuilds `data/guide_state.json` from the configured M3U/XMLTV inputs |

## Guide Configuration

| Field | Description |
|---|---|
| Playlist Source | Path or HTTP/HTTPS URL to the M3U playlist |
| XMLTV Source | Path or HTTP/HTTPS URL to the XMLTV file |
| Channel Group | Optional exact match against the M3U `group-title` attribute |
| Timezone | UI setting currently exposed as `local` or `utc` |

For a first test, leave the default sample sources in place.

## Render Settings

| Field | Description |
|---|---|
| Resolution | `1280x720` or `1920x1080` |
| Frame Rate | Render and encode FPS; default is `15` |
| Segment Length | HLS segment duration in seconds; default is `6` |
| Guide Duration | Time horizon shown across the grid; default is `90` minutes |
| Visible Rows | Number of channels displayed per page; default is `8` |
| Page Dwell | Seconds each page remains visible; default is `12` |
| Transition | `cut` or vertical `scroll` transition between pages |
| Output Format | Exposed option for M3U/XMLTV preference; v1.0.0 still provides the built-in output endpoints |

Use lower FPS and 720p when testing on limited hardware.

## Theme Selection

The theme dropdown lists directories under `app/themes/`. Selecting a theme updates the admin color preview immediately. Save the config to apply it to the generated guide output.

The guide title controls:

- Rendered title in the guide video
- Channel name in `/channel.m3u`
- Display name and programme title in `/channel.xmltv`

## Stream Outputs

The admin UI displays copyable URLs for:

| Output | Endpoint |
|---|---|
| HLS Stream | `/hls/master.m3u8` |
| M3U Playlist | `/channel.m3u` |
| XMLTV Guide | `/channel.xmltv` |

Use `/channel.m3u` for RetroIPTVGuide import.

## Pipeline Status

The status panel reports whether the renderer and FFmpeg processes are running, whether the pipeline is active, and whether the guide has enough HLS buffer to switch from standby to live guide playback.

`guide_buffered` becomes true only after:

- `output/guide.m3u8` exists
- enough live guide segments are listed
- the configured minimum buffer age has passed after a fresh start

## Preview panel

The admin page includes an HLS preview player using hls.js when needed. The player loads `/hls/master.m3u8`, the same stream path used by IPTV clients.

The preview includes client-side recovery logic:

- retries fatal media errors
- reinitializes when playback stalls
- polls `/status` and reloads when `stream_version` changes

## Tabs

### General

The main configuration and stream output controls.

### Music

Upload audio files and configure background music. See [Background Music](BACKGROUND_MUSIC.md).

### Diagnostics

Tune HLS live-edge delay, minimum buffer requirements, standby playlist size, and log display length. See [Diagnostics and Logging](DIAGNOSTICS_AND_LOGGING.md).

### Logs

View recent app events and download the full event log as JSONL or CSV.

### About

Shows the application name, version, and description.
