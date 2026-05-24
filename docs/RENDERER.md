# Renderer

RetroStation MC v1.0.0 uses a Python/Pillow renderer implemented in `app/renderer.py`.

## Renderer contract

The renderer consumes:

```text
data/guide_state.json
```

It produces:

```text
raw RGB frames on stdout
```

The manager starts it with arguments similar to:

```bash
python app/renderer.py \
  --state data/guide_state.json \
  --fps 15 \
  --resolution 1280x720
```

FFmpeg reads this stdout stream as `rgb24` raw video.

## State reload behavior

The renderer watches the state file modification time. When `guide_state.json` changes, it reloads state and rebuilds cached static layers.

This allows many guide/content/theme changes to apply without restarting FFmpeg.

## Page model

The state builder splits channels into pages based on `visible_rows`. The renderer rotates through those pages using the configured `page_seconds` dwell time.

Supported transition modes:

- `cut`
- `scroll`

The scroll transition duration is defined in the renderer as `SCROLL_SECS = 4.0`.

## Layer caching

The renderer is structured around cached layers:

- Static frame layer: background, header/footer chrome, borders, static labels
- Static content layers: page-specific channel rows and programme blocks
- Dynamic layer: clock, current-time line, transition offsets

The page cache is capped to prevent unbounded memory growth.

## Program text fit behavior

The renderer hides or abbreviates programme text when the rendered cell is too narrow. Constants in `renderer.py` define small-width thresholds:

| Constant | Purpose |
|---|---|
| `PROGRAM_TEXT_HIDE_WIDTH` | Width below which program text is hidden |
| `PROGRAM_TEXT_ABBREV_WIDTH` | Width below which program text is abbreviated |

## Channel-name abbreviation

`abbreviate_channel_name()` shortens channel names for display in the channel column. This helps preserve visual density in the guide grid.

## Timing model

The renderer advances frames against a frame deadline to reduce cumulative drift. It can emit telemetry when started with `--telemetry`; the manager passes that flag when `RETRO_TELEMETRY_DEBUG` is enabled.

## Replacement strategy

The renderer is the most replaceable part of the architecture. A future implementation can preserve the same input/output contract:

```text
guide_state.json in → raw RGB frames out
```

That allows the Flask admin, config database, and HLS serving model to remain stable while replacing the frame generation engine.
