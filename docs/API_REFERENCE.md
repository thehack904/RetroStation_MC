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
| `GET` | `/virtual-channels` | Virtual Channels admin page |
| `POST` | `/virtual-channels/weather/config` | Save Weather Channel settings |
| `POST` | `/virtual-channels/news/config` | Save News Now enablement, output profile, and up to six RSS/Atom feeds |
| `GET` | `/api/news` | Return normalized headlines for the synchronized current News feed slot |
| `GET` | `/news` | Browser preview of the News Now presentation |
| `GET` | `/hls/news.m3u8` | Generated News Now HLS channel |
| `POST` | `/off-air/settings` | Save off-air schedule settings |
| `POST` | `/guide-preview/detect-transport` | Probe an HTTP/HTTPS Guide Preview URL and return `hls`, `mpegts`, or `unknown` without starting the Guide |
| `POST` | `/guide-preview/settings` | Save Guide Preview source, transport cache, aspect, audio, and related settings; optionally restart/start the Guide |
| `POST` | `/guide-message/settings` | Save Guide Message text/timing and apply it live to a running Guide |
| `POST` | `/guide-preview/url-channels` | Parse a remote M3U URL and return selectable Preview channels |
| `POST` | `/guide-preview/upload` | Upload/select a local Guide Preview video |
| `POST` | `/guide-preview/remove` | Remove the selected local Guide Preview video |
| `POST` | `/virtual-channels/channel-mix/config` | Save Channel Mix membership, timing, output, logo, and audio settings |
| `POST` | `/virtual-channels/traffic/config` | Save Simulated Traffic settings |

### `POST /guide-preview/detect-transport`

Request body:

```json
{"url": "http://example.invalid/channel"}
```

Successful detection returns `{"transport":"hls"}` or `{"transport":"mpegts"}`. An inconclusive probe returns HTTP 200 with `{"transport":"unknown", ...}` so the settings-save path can retry. Non-HTTP(S) values return HTTP 400. Local uploaded files are classified internally as `file` and do not use this endpoint. The result is an **input** classification only; it does not select the Guide/Virtual Channel output format.

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
| `GET` | `/channel.m3u` | M3U playlist for the guide plus any enabled virtual channels, MIME `application/x-mpegURL` |
| `GET` | `/channel.m3u8` | Same playlist, MIME `application/vnd.apple.mpegurl` |
| `GET` | `/channel.xmltv` | XMLTV guide for the guide plus any enabled virtual channels |
| `GET` | `/discover.json` | HDHomeRun HTTP discovery metadata when **HDHomeRun Export** is enabled; includes `DeviceID`, `BaseURL`, `LineupURL`, and tuner count |
| `GET` | `/device.xml` | Optional HDHomeRun-compatible device description when **HDHomeRun Export** is enabled |
| `GET` | `/lineup_status.json` | Optional HDHomeRun-compatible lineup status when **HDHomeRun Export** is enabled |
| `GET` | `/lineup.json` | HDHomeRun-compatible lineup containing RSMC-owned channels plus any imported source channels explicitly selected for rebroadcast |
| `GET` | `/hdhr/guide.xml` | HDHomeRun-specific XMLTV matching the effective tuner lineup |
| `GET` | `/hdhr/xmltv.xml` | Alias for `/hdhr/guide.xml` |
| `GET`, `HEAD` | `/hdhr/channel/<channel_index>` | Continuous MPEG-TS tune URL for the matching RSMC-owned or selected source channel |
| `GET` | `/weather` | Browser-rendered Weather channel preview page |
| `GET` | `/api/weather` | Weather overlay payload for the Weather channel page |
| `GET` | `/weather-radar/current.png` | Current rendered Weather radar image when available |
| `GET`, `POST`, `DELETE` | `/api/weather/bg_override` | Read/set/clear Weather background-condition override |
| `GET` | `/api/weather/zip-lookup` | Resolve Weather location metadata from a ZIP code |
| `GET` | `/traffic` | Browser preview of Simulated Traffic |
| `GET` | `/api/traffic` | Current Simulated Traffic payload |
| `GET` | `/api/traffic/roads/<city_id>` | Road GeoJSON for a Traffic city |
| `POST` | `/api/traffic/cities/<city_id>` | Enable/disable or update one Traffic city |
| `POST` | `/api/traffic/cities/enable-all` | Enable all Traffic cities |
| `POST` | `/api/traffic/cities/disable-all` | Disable all Traffic cities |
| `GET` | `/traffic-map/<filename>` | Serve a Traffic basemap asset |
| `GET` | `/api/channel_mix` | Return current Channel Mix state; `/api/channel-mix` is an alias |

## HLS endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/hls/master.m3u8` | HLS master playlist that selects standby or live media |
| `GET` | `/hls/standby.m3u8` | Synthetic standby media playlist; returns 404 once live is ready |
| `GET` | `/hls/live.m3u8` | Live guide media playlist; returns 404 until live is ready |
| `GET` | `/hls/weather.m3u8` | Weather virtual channel HLS playlist with standby fallback while warming up |
| `GET` | `/hls/traffic.m3u8` | Simulated Traffic HLS playlist |
| `GET` | `/hls/news.m3u8` | News Now HLS playlist |
| `GET` | `/hls/channel-mix.m3u8` | Channel Mix HLS playlist |
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
| `POST` | `/virtual-channels/weather/music/upload` | Upload one or more Weather Channel audio files |
| `POST` | `/virtual-channels/weather/music/delete/<filename>` | Delete an uploaded Weather Channel audio file |

## Artwork and standby endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/guide-logo/<filename>` | Serve guide logo files from `data/guide_logo/` |
| `POST` | `/guide-logo/upload` | Upload a custom guide logo |
| `POST` | `/guide-logo/remove` | Remove the current custom guide logo |
| `GET` | `/weather-logo/<filename>` | Serve Weather logo files from `data/weather_logo/` |
| `GET` | `/traffic-logo/<filename>` | Serve Simulated Traffic logo files |
| `GET` | `/news-logo/<filename>` | Serve News Now logo files |
| `GET` | `/channel-mix-logo/<filename>` | Serve Channel Mix logo files |
| `GET` | `/standby-pattern/<filename>` | Serve uploaded standby pattern images |
| `POST` | `/standby-pattern/upload` | Upload a standby pattern image |
| `POST` | `/standby-pattern/select` | Select the active uploaded standby pattern |
| `POST` | `/standby-pattern/select-default` | Revert to the generated default standby pattern |
| `POST` | `/standby-pattern/remove` | Delete an uploaded standby pattern image |
| `POST` | `/standby-pattern/settings` | Save standby overlay toggle and opacity |

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
| `gpu_capabilities` | Hardware detection, encoder readiness, and active-path information for the admin UI |


## HDHomeRun network discovery

When **HDHomeRun Export** is enabled, RetroStation MC also listens for the native SiliconDust/libhdhomerun discovery protocol on **UDP port 65001**. This is separate from the HTTP `/discover.json` route. Discovery responses advertise the RSMC HTTP service (normally `http://SERVER_IP:8787`) and `/lineup.json`.

The generated DeviceID is persisted as an 8-character hexadecimal HDHomeRun-style ID with a valid libhdhomerun checksum. Existing UUID-style IDs from early v1.4.0 builds are migrated deterministically.

For Linux validation, install the SiliconDust command-line utility and run:

```bash
hdhomerun_config discover
```

The RSMC tuner should be returned when the client is on the same subnet and UDP 65001 is not blocked by a host/network firewall.


### `GET /api/channel_mix`

Returns Channel Mix playout state including display name, configured ordered members, nominal scheduled member, active available member, seconds until the next wall-clock boundary, and total cycle length. Alias: `/api/channel-mix`.

### `GET /hls/channel-mix.m3u8`

CH 5 HLS entrypoint. The playlist is selected in the RSMC playout layer from the currently active configured virtual channel. Disabled or unbuffered members fall forward to the next available member without changing the wall-clock schedule.
