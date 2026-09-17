# HLS Pipeline

RetroStation MC uses FFmpeg to produce HLS media and Flask to serve client-facing playlists.

## Output hierarchy

```text
/channel.m3u
   └── /hls/master.m3u8
           ├── /hls/standby.m3u8
           │       └── /hls/standby.ts
           └── /hls/live.m3u8
                   └── /hls/guide_*.ts
/channel.m3u (Weather enabled)
   └── /hls/weather.m3u8
           └── /hls/weather_*.ts
```

`/hls/guide.m3u8` also exists as a backward-compatible unified media playlist endpoint.

## Default HLS profile

The default FFmpeg profile is `software_default`.

| Setting | Default |
|---|---:|
| Resolution | `1280x720` |
| FPS | `15` |
| Segment duration | `6` seconds |
| FFmpeg HLS list size | `10` segments |
| Live-edge delay | `2` segments |
| Minimum live buffer age | `18` seconds |
| Minimum live segment count | `3` |
| Standby playlist window | `3` synthetic entries |

The hardware acceleration mode controls profile resolution:

- `software_fallback`: force software encoding (`libx264`)
- `hardware_if_available`: use the first detected encoder-ready provider (NVIDIA, Intel, VAAPI, AMD priority order), otherwise fall back to software

The same profile resolution path is used for both guide and Weather channel pipelines.

## FFmpeg video strategy

The manager launches FFmpeg with raw video input:

```text
-f rawvideo -pix_fmt rgb24 -s <resolution> -r <fps> -i -
```

It encodes video using:

```text
-c:v libx264
-preset veryfast
-tune zerolatency
-pix_fmt yuv420p
```

## Keyframe strategy

The app uses a two-second keyframe interval:

```text
-g <fps * 2>
-keyint_min <fps * 2>
-force_key_frames expr:gte(t,n_forced*2)
-sc_threshold 0
```

This is intended to keep HLS segment boundaries clean and reduce player stalls caused by segments that start without a usable keyframe.

## HLS flags

FFmpeg writes HLS using:

```text
-f hls
-hls_segment_type mpegts
-hls_list_size 10
-segment_list_flags +live
-hls_flags delete_segments+program_date_time+omit_endlist+discont_start+independent_segments
```

The important behaviors are:

| Flag | Purpose |
|---|---|
| `delete_segments` | Prevents unbounded segment accumulation |
| `program_date_time` | Adds wall-clock tags before Flask strips them from served live playlists |
| `omit_endlist` | Keeps stream treated as live |
| `discont_start` | Marks pipeline restart boundaries cleanly |
| `independent_segments` | Signals segments are independently decodable |

## Epoch-based media sequence

On each pipeline start, FFmpeg receives:

```text
-start_number <epoch_seconds / segment_seconds>
```

This keeps `EXT-X-MEDIA-SEQUENCE` increasing across restarts. Without this, clients may see sequence numbers reset and stall while waiting for segments they believe they already consumed.

## Standby mode

The app generates `output/standby.ts`, a 30-second color-bar style MPEG-TS segment with silent AAC audio.
It also generates `output/static.ts`, a static-noise segment used when off-air static mode is enabled.
While live guide output is not ready, `/hls/master.m3u8` points at `/hls/standby.m3u8`.

The standby media playlist is synthetic. It repeats `standby.ts` with a changing query string and discontinuity tags so clients continue polling and do not cache a single exhausted segment forever.

During configured off-air windows:

- `/hls/master.m3u8` always points to `/hls/standby.m3u8`
- `/hls/live.m3u8` returns `404`
- `/hls/standby.m3u8` serves either `standby.ts` or `static.ts` depending on `off_air_static_enabled`

## Live readiness gate

The live guide is considered ready only when:

1. `output/guide.m3u8` exists.
2. The playlist contains at least `diag_min_buffer_segments` live guide segment entries.
3. If the pipeline was freshly started, at least `diag_min_buffer_secs` seconds have elapsed.
4. The pipeline is marked active.

Until all checks pass, the master playlist stays pointed at standby.

## Standby-to-live transition

When readiness changes, `stream_version` increments. The master playlist appends `?v=<stream_version>` to the selected media playlist URL.

This gives clients a changed URL when switching between standby and live modes:

```text
/hls/standby.m3u8?v=1
/hls/live.m3u8?v=2
```

The admin preview polls `/status` and reloads the HLS player when the version changes.

## Delayed live edge

`/hls/live.m3u8` reads `output/guide.m3u8` and removes the newest segments according to `diag_delay_segments`, while preserving at least a minimum number of visible segments.

This keeps clients away from the most recently written HLS edge, where file write timing and player polling can otherwise cause spinner/catch-up behavior.

## Backward-compatible endpoint

`/hls/guide.m3u8` returns live output when ready, otherwise standby. New integrations should prefer `/hls/master.m3u8`.

## Weather channel behavior

When the Weather channel is enabled, `/hls/weather.m3u8` is exposed as a dedicated media playlist.
It follows the same guide readiness principles and uses standby fallback while weather output is warming up.

## Guide preview composition and audio

Guide Preview input transport is resolved before the Guide pipeline is built. Local uploads are `file`. HTTP/HTTPS sources are classified as `hls`, `mpegts`, or unknown with a bounded detector. The result is cached only for the exact selected source key, so selecting a different URL/channel invalidates stale detection.

The detected **input** transport chooses one of two internal processing paths:

- **HLS / local file / unknown network input:** one shared FFmpeg preview worker normalizes video to the Guide frame rate and a 640x360 or 640x480 `yuv420p` canvas, continuously replacing `data/guide_preview/latest-preview.jpg`. The renderer samples that frame without blocking. In **Preview** audio mode, the same source session resamples audio (`async=1`, 48 kHz stereo AAC) and tees MPEG-TS audio to local UDP relays consumed by the primary/secondary Guide FFmpeg processes. Detected HLS sources are realtime-paced; local files loop in realtime.
- **Detected MPEG-TS network input:** a dedicated FFmpeg relay normalizes video/audio and writes a short local one-second-segment HLS relay (`data/guide_preview/mpegts-preview.m3u8` plus `mpegts-preview-*.ts`). The Guide FFmpeg process overlays that relay directly; Preview audio is mapped from the same relay input. This preserves the transport-specific path used for sustained MPEG-TS testing.

Preview audio mode remains independent:

- `guide`: use the normal configured Guide Channel music/silence path.
- `preview`: use Preview-source audio from the matching transport-specific path when available.
- `silent`: suppress Guide music and Preview audio and emit the normal compatibility-silence path.

Preview enablement, source, transport cache, audio mode, or effective aspect changes are FFmpeg-level settings and require a pipeline rebuild. Auto aspect probing remains separate: it starts with the cached result or 16:9 fallback and performs bounded background aspect detection without delaying Guide startup.

This transport routing applies only to **Guide Preview inputs**. The Guide's generated stream remains HLS with MPEG-TS segments; selectable HLS versus continuous MPEG-TS Guide/Virtual Channel output is not implemented here.

