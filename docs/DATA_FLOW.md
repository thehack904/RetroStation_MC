# Data Flow

This document describes how source data becomes a playable guide channel.

## 1. Configuration input

The admin UI saves settings to `data/config.db`. Key inputs are:

- M3U playlist source
- XMLTV source
- theme name
- render resolution and FPS
- HLS segment length
- guide horizon and row count
- optional channel group filter
- optional background music configuration

## 2. M3U parsing

`app/m3u_parser.py` reads local files or HTTP/HTTPS URLs. It recognizes `#EXTINF` attributes and pairs each entry with the next non-comment URL.

Each parsed channel includes:

```json
{
  "id": "channel id",
  "name": "channel name",
  "number": "channel number",
  "group": "group title",
  "logo": "logo url or path",
  "stream_url": "source stream url"
}
```

The source stream URL is parsed but not directly restreamed by RetroStation MC v1.4.0. The app uses playlist and EPG metadata to render the guide channel. v1.4.0 also adds an internal playout document and scheduler layer for future scheduled video, promo, virtual channel, standby, and Preview Channel sequencing.

## 3. XMLTV parsing

`app/xmltv_parser.py` reads local files or HTTP/HTTPS URLs and parses XMLTV `<programme>` entries.

Each parsed programme includes:

```json
{
  "title": "Program title",
  "desc": "Program description",
  "start": "UTC ISO timestamp",
  "stop": "UTC ISO timestamp"
}
```

Programmes are grouped by XMLTV channel ID.

## 4. Guide state generation

`app/guide_state.py` builds `data/guide_state.json`.

The builder:

1. Aligns the visible guide start to the current 30-minute boundary.
2. Applies the configured guide horizon.
3. Filters channels by exact group name when `channel_group` is set.
4. Selects programmes overlapping the visible time window.
5. Adds a `No guide data` block for channels without visible programmes.
6. Splits channels into pages based on `visible_rows`.
7. Embeds theme data from `app/themes/<theme>/theme.json`.


## 5. Playout scheduling foundation

v1.4.0 adds a playout document schema and basic scheduler. This layer is separate from the current guide-state renderer path and is intended to become the future bridge between scheduled content and the Preview Channel renderer.

The playout document flow is:

```text
playout JSON
    │
    ▼
app/playout_schema.py validation
    │
    ▼
app/playout_scheduler.py active/next item state
    │
    ▼
app/playout_fallback.py availability check and standby fallback
    │
    ▼
future renderer/transcoder consumer
```

The current guide-rendering path remains based on `data/guide_state.json`.

## 6. Rendering

`app/renderer.py` loads `guide_state.json`, renders frames using Pillow, and writes raw `rgb24` frames to stdout.

Renderer output is continuous. Dynamic elements such as the clock and current-time line update per frame.

## 7. Encoding and HLS packaging

`ffmpeg` reads raw RGB frames from the renderer and writes:

```text
output/guide.m3u8
output/guide_<number>.ts
```

Video is encoded as H.264. Audio is encoded as AAC, either from uploaded music or generated silence.

## 8. HLS serving

Flask serves a stable master playlist:

```text
/hls/master.m3u8
```

The master playlist selects standby or live media based on buffer readiness:

```text
/hls/standby.m3u8  # before live content is ready
/hls/live.m3u8     # once enough live guide buffer exists
```

## 9. IPTV import

Flask also generates:

```text
/channel.m3u
/channel.xmltv
```

These provide the stable virtual channel identity used by RetroIPTVGuide or another IPTV client.
