# Configuration Reference

Configuration is stored in SQLite at:

```text
data/config.db
```

The `settings` table stores one key per setting. Values are JSON-encoded.

## Defaults

| Key | Default | Description |
|---|---:|---|
| `playlist_source` | `sample_data/channels.m3u` | M3U input path or URL |
| `xmltv_source` | `sample_data/xmltv.xml` | XMLTV input path or URL |
| `theme` | `retrostation_mc` | Theme directory name under `app/themes/` |
| `hardware_acceleration_mode` | `software_fallback` | Encoding mode: `software_fallback` or `hardware_if_available` |
| `aspect_ratio` | `16:9` | Channel output aspect ratio: `16:9` (widescreen) or `4:3` (standard) |
| `resolution` | `1280x720` | Render and encode resolution: 1280x720 / 1920x1080 widescreen or 960x720 / 1440x1080 standard 4:3 |
| `guide_secondary_enabled` | `false` | Enable a second native Guide HLS output using the same Guide data/theme and encoder policy as the primary output |
| `guide_secondary_resolution` | `720x480` | Secondary Guide resolution: `720x480` (default), `640x480`, or `960x720` |
| `fps` | `15` | Render and encode frame rate |
| `segment_seconds` | `6` | HLS target segment duration |
| `page_seconds` | `12` | Seconds each guide page stays on screen |
| `visible_rows` | `8` | Channels per guide page |
| `guide_minutes` | `90` | Guide horizon shown across the grid |
| `channel_group` | empty | Optional exact M3U group-title filter |
| `title` | `Guide Channel` | Output title and virtual channel name |
| `timezone` | `local` | Exposed UI option; parser normalizes XMLTV times to UTC internally |
| `browser_timezone` | empty | IANA timezone captured from the admin browser when `timezone` is `local` |
| `output_format` | `both` | UI output preference field |
| `transition` | `scroll` | Page transition mode: `scroll` or `cut` |
| `guide_logo_mode` | `default` | `default`, `custom`, or `disabled` for exported guide-channel logo metadata |
| `guide_logo_custom_file` | empty | Selected custom guide logo filename stored under `data/guide_logo/` |
| `guide_preview_enabled` | `false` | Enable the optional Guide Channel video preview/information layout |
| `guide_preview_source_type` | `file` | Preview source type: local `file`, `url`, or an enabled RSMC virtual channel |
| `guide_preview_file` | empty | Selected uploaded preview filename under `data/guide_preview/` |
| `guide_preview_url` | empty | Direct HTTP/HLS stream URL or M3U playlist URL when source type is `url` |
| `guide_preview_url_channel` | empty | Selected stream URL from `guide_preview_url` when it is an M3U playlist |
| `guide_preview_url_channel_name` | empty | Display name of the selected M3U playlist channel |
| `guide_preview_audio_mode` | `guide` | Preview audio mode: `guide`, `preview`, or `silent` |
| `guide_preview_aspect_mode` | `auto` | Preview window aspect mode: `auto`, `16:9`, or `4:3` |
| `guide_preview_detected_aspect_ratio` | empty | Cached Auto detection result (`16:9` or `4:3`); empty uses immediate 16:9 fallback |
| `guide_preview_detected_source_key` | empty | Source identity associated with the cached Auto detection so stale results are not reused after source changes |
| `standby_custom_file` | empty | Active uploaded standby pattern filename (empty uses generated default) |
| `standby_overlay_enabled` | `true` | Whether custom standby images render the standby text band/title overlay |
| `standby_overlay_opacity` | `50` | Black standby text-band opacity percent when custom standby image is active |
| `off_air_enabled` | `false` | Enable the daily off-air time window |
| `off_air_start` | `00:00` | Local `HH:MM` time when the channel goes off-air |
| `off_air_end` | `06:00` | Local `HH:MM` time when the channel returns on-air |
| `off_air_static_enabled` | `false` | When off-air, play static noise instead of the standby test pattern |
| `music_mode` | `none` | `none`, `single`, or `playlist` |
| `music_loop` | `false` | Whether selected music loops |
| `music_single_file` | empty | Selected filename for single-track mode |
| `music_playlist_files` | `[]` | Ordered filenames for playlist mode |
| `weather_channel_enabled` | `false` | Enable the Weather Channel virtual channel |
| `weather_logo_enabled` | `true` | Include the weather channel icon (`tvg-logo`) in exported M3U playlists |
| `weather_lat` | empty | Latitude for weather data lookup |
| `weather_lon` | empty | Longitude for weather data lookup |
| `weather_location_name` | empty | Display name for the weather location |
| `weather_units` | `F` | Temperature units: `F` (Fahrenheit) or `C` (Celsius) |
| `weather_seconds_per_segment` | `300` | Seconds displayed on each of the 4 weather screens before rotating |
| `weather_bg_condition_override` | empty | Force a specific animated background condition for testing |
| `weather_music_mode` | `none` | Weather channel music mode: `none`, `single`, or `playlist` |
| `weather_music_loop` | `false` | Whether Weather channel selected music loops |
| `weather_music_single_file` | empty | Selected filename for Weather channel single-track mode |
| `weather_music_playlist_files` | `[]` | Selected filenames for Weather channel playlist mode |
| `diag_delay_segments` | `2` | Segments hidden from the live edge before serving to clients |
| `diag_min_buffer_secs` | `18` | Minimum age before fresh pipeline switches to live |
| `diag_min_buffer_segments` | `3` | Minimum live guide segment count before switching to live |
| `diag_standby_window_segments` | `3` | Number of synthetic standby playlist entries |
| `diag_log_tail_lines` | `120` | Recent event count shown in the admin UI |

## Settings that require pipeline restart

The manager treats these as FFmpeg-level settings:

- `aspect_ratio`
- `resolution`
- `fps`
- `segment_seconds`
- `hardware_acceleration_mode`
- `music_mode`
- `music_loop`
- `music_single_file`
- `music_playlist_files`
- `guide_preview_enabled`
- `guide_preview_source_type`
- `guide_preview_file`
- `guide_preview_url`
- `guide_preview_url_channel`
- `guide_preview_url_channel_name`
- `guide_preview_audio_mode`
- `guide_preview_aspect_mode`
- `guide_preview_detected_aspect_ratio`
- `guide_preview_detected_source_key`
- `weather_music_mode`
- `weather_music_loop`
- `weather_music_single_file`
- `weather_music_playlist_files`

Changing these should be followed by the relevant pipeline restart.

## Settings that can refresh without full restart

Theme, title, guide content, page dwell, visible rows, guide duration, channel group, and transition are read through the renderer state. When the pipeline is active, saving configuration refreshes guide state so the renderer can pick up changes without necessarily rebuilding the FFmpeg process.

## Hardware acceleration behavior

`hardware_acceleration_mode` controls both the main guide pipeline and the Weather channel pipeline:

- `software_fallback` forces `libx264`
- `hardware_if_available` uses encoder-ready hardware when FFmpeg validation succeeds, otherwise software fallback remains active

The admin Hardware Acceleration tab reports detected devices separately from encoder-ready providers and shows the currently active path.

## M3U input expectations

RetroStation MC parses standard `#EXTINF` entries and reads these attributes when available:

| Attribute | Used as |
|---|---|
| `tvg-id` | Channel/programme ID |
| `tvg-name` | Channel name |
| `tvg-chno` | Channel number |
| `group-title` | Channel group filter |
| `tvg-logo` | Stored in channel state for future use |

The parser uses the line after `#EXTINF` as the channel stream URL.

## XMLTV input expectations

RetroStation MC reads XMLTV `<programme>` elements and uses:

| XMLTV field | Used as |
|---|---|
| `programme@channel` | Channel ID matching M3U `tvg-id` |
| `programme@start` | Program start time |
| `programme@stop` | Program stop time |
| `<title>` | Program title |
| `<desc>` | Program description |

Supported XMLTV date formats:

- `%Y%m%d%H%M%S %z`
- `%Y%m%d%H%M%S`
- `%Y%m%d%H%M %z`
- `%Y%m%d%H%M`

Dates without timezone data are treated as UTC.

## Channel group filtering

`channel_group` is an exact string match against the parsed M3U `group-title` value. Leave it blank to include all channels.

## No-guide-data fallback

If a channel has no XMLTV programmes in the current rendered time window, the state builder adds a placeholder program:

```text
No guide data
```

This keeps the grid visually complete instead of leaving empty rows.


### Plex Live TV compatibility

When using the RSMC HDHomeRun-compatible tuner with Plex, ensure Plex's **Disable video stream transcoding** option is not enabled. Live TV playback may need Plex to perform its own transcode/remux decision even when RSMC already delivers H.264/AAC MPEG-TS.


## News Now virtual channel

News Now is RSMC channel 4 and supports up to six HTTP(S) RSS/Atom feed URLs. Feeds divide a synchronized 30-minute wall-clock block equally (six feeds = five minutes each; three = ten minutes each; one = thirty minutes). Results are cached to limit polling. Configure enablement, 16:9/4:3 aspect ratio, HD/SD resolution, and feed URLs on **Virtual Channels**. RSMC owns all News configuration/state and exposes the generated channel through `/hls/news.m3u8`, normal M3U/XMLTV export, and optional HDHomeRun output.

The feed-slot timing, multi-feed semantics, and RSS/Atom model were adapted from RetroIPTVGuide v4.9.9-dev. Browser-only overlay assumptions were replaced with RSMC's native renderer → FFmpeg → HLS playout pipeline.


## Channel Mix

Channel Mix is RSMC virtual channel 5. Configure an ordered set of RSMC-owned virtual channels and a 1–1440 minute duration for each. The schedule uses Unix wall-clock time modulo the complete cycle, so all viewers tune to the same nominal slot. If a selected source is disabled or not buffered, RSMC temporarily falls forward to the next available selected source without changing the wall-clock schedule. Channel Mix operates at the HLS playout layer and reuses source-channel streams/provider caches rather than starting duplicate Weather, Traffic, or News fetchers.


## Shared Music Library

RSMC stores uploaded background music centrally in `data/music/`. Upload files from the **Shared Music** tab on the main admin page. The library accepts MP3 and the existing compatible audio formats supported by RSMC.

Music selection is independent per generated channel:

- Guide Channel
- Weather Channel
- Simulated Traffic
- News Now

Each generated channel can use **None (silence)**, a **Single file**, **Selected files**, or **All shared music**, with an independent loop setting. Channel Mix has its own audio override: member-channel audio is dropped and the Mix selection is muxed continuously over the selected member video.

Legacy Weather-specific music files under `data/weather_music/` are copied into the shared library at startup when the destination filename does not already exist.

## HDHomeRun / Plex status in v1.4.0 development

The HDHomeRun emulation implementation remains in the codebase, but its admin controls are temporarily hidden and the feature is forced disabled while Plex compatibility work is deferred. This does **not** disable RSMC's HLS segmenter or standard M3U/XMLTV outputs. `/channel.m3u` and the individual `/hls/*.m3u8` channels remain the supported output path for RetroStation Player, RetroIPTVGuide, TiViMate, VLC, and similar IPTV clients.
