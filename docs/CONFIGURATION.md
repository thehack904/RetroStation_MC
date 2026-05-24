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
| `theme` | `classic_blue` | Theme directory name under `app/themes/` |
| `resolution` | `1280x720` | Render and encode resolution |
| `fps` | `15` | Render and encode frame rate |
| `segment_seconds` | `6` | HLS target segment duration |
| `page_seconds` | `12` | Seconds each guide page stays on screen |
| `visible_rows` | `8` | Channels per guide page |
| `guide_minutes` | `90` | Guide horizon shown across the grid |
| `channel_group` | empty | Optional exact M3U group-title filter |
| `title` | `Guide Channel` | Output title and virtual channel name |
| `timezone` | `local` | Exposed UI option; parser normalizes XMLTV times to UTC internally |
| `output_format` | `both` | UI output preference field |
| `transition` | `scroll` | Page transition mode: `scroll` or `cut` |
| `music_mode` | `none` | `none`, `single`, or `playlist` |
| `music_loop` | `false` | Whether selected music loops |
| `music_single_file` | empty | Selected filename for single-track mode |
| `music_playlist_files` | `[]` | Ordered filenames for playlist mode |
| `diag_delay_segments` | `2` | Segments hidden from the live edge before serving to clients |
| `diag_min_buffer_secs` | `18` | Minimum age before fresh pipeline switches to live |
| `diag_min_buffer_segments` | `3` | Minimum live guide segment count before switching to live |
| `diag_standby_window_segments` | `3` | Number of synthetic standby playlist entries |
| `diag_log_tail_lines` | `120` | Recent event count shown in the admin UI |

## Settings that require pipeline restart

The manager treats these as FFmpeg-level settings:

- `resolution`
- `fps`
- `segment_seconds`
- `music_mode`
- `music_loop`
- `music_single_file`
- `music_playlist_files`

Changing these should be followed by a pipeline restart.

## Settings that can refresh without full restart

Theme, title, guide content, page dwell, visible rows, guide duration, channel group, and transition are read through the renderer state. When the pipeline is active, saving configuration refreshes guide state so the renderer can pick up changes without necessarily rebuilding the FFmpeg process.

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
