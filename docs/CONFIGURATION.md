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
| `resolution` | `1280x720` | Render and encode resolution |
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
