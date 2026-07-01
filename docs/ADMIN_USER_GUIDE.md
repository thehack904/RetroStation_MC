# Admin User Guide

The admin UI is available at the root path:

```text
http://YOUR_SERVER:8787/
```

v1.3.0 still has no login screen. Keep the app local-only or place it behind external authentication.

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
| Timezone | `local` displays times in the browser's detected local timezone (IANA name auto-detected on page load, e.g. `America/New_York`); `utc` displays times in UTC |
| Guide Icon (M3U) | Controls whether `/channel.m3u` exports the default icon from `/data/guide_logo`, a custom uploaded icon, or no icon (`tvg-logo`) |

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
| Hardware Acceleration | `software_fallback` always uses `libx264`; `hardware_if_available` uses encoder-ready hardware and falls back automatically when needed |
| Output Format | UI export preference only; `/channel.m3u`, `/channel.m3u8`, and `/channel.xmltv` remain available |
| Weather Channel | Adds the Weather virtual channel as its own entry in `/channel.m3u` and `/channel.xmltv` when enabled |

Use lower FPS and 720p when testing on limited hardware.

## Theme Selection

The theme dropdown lists directories under `app/themes/`. Selecting a theme updates the admin color preview immediately. Save the config to apply it to the generated guide output.

The guide title controls:

- Rendered title in the guide video
- Guide channel name in `/channel.m3u`
- Guide channel display name and programme title in `/channel.xmltv`

## Stream Outputs

The admin UI displays copyable URLs for:

| Output | Endpoint |
|---|---|
| HLS Stream | `/hls/master.m3u8` |
| M3U Playlist | `/channel.m3u` |
| XMLTV Guide | `/channel.xmltv` |

Use `/channel.m3u` for RetroIPTVGuide import. When **Weather Channel** is enabled on the main admin page, both exports include a second Weather entry that points to `/hls/weather.m3u8`.

## Virtual Channels page

Open `/virtual-channels` to manage Weather Channel-specific settings:

- enable/disable the Weather virtual channel
- set location, units, and rotation timing
- toggle **Show Weather Icon in M3U / XMLTV** for exported playlist `tvg-logo` metadata
- upload Weather-only background music and choose single-track or playlist playback
- preview the browser-rendered Weather channel page

## Guide icon for RetroIPTVGuide

Use **Guide Icon (M3U)** to choose:

- **Default guide icon** (served from `/data/guide_logo`, typically `default.png`)
- **Uploaded custom icon**
- **Disabled** (no icon metadata in exported M3U)

Upload/remove custom icons in the **Custom Guide Icon Upload** section on the admin page.

## Hardware acceleration

The main configuration form selects the encoding mode, and the **Hardware Acceleration** tab explains what the app can actually use:

- **Detected Devices** lists hardware that was found on the system.
- **Hardware-Ready Providers** lists devices that also passed FFmpeg encoder validation.
- **Selection Mode** shows whether the app is forced to software or allowed to auto-select hardware.
- **Active Path** and **Active Status** explain which encoder the guide is using right now.

## Standby test pattern graphics

In the **Standby Pattern** tab, you can upload multiple standby test pattern image files and choose which pattern is active from a dropdown.

- The dropdown includes **Default generated pattern** so you can switch back without an uploaded file.
- The active uploaded file is used as the standby background image.
- **Show standby text overlay** lets you disable the title/subtitle and text band so only the graphic is shown.
- **Overlay transparency** controls the black standby text band opacity (used only when a custom standby image is active).

## Off-air display mode

In the **Off Air** tab, enable a daily off-air schedule and choose what viewers see during that window:

- **Play static noise while off-air** enabled: serves a TV static clip.
- **Play static noise while off-air** disabled: serves the standby test pattern.

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

### Hardware Acceleration

View detected GPU providers, encoder-ready hardware, and the currently active encode path.

### Logs

View recent app events and download the full event log as JSONL or CSV.

### About

Shows the application name, version, and description.
