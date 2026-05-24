# Roadmap

This roadmap lists recommended post-v1.0.0 improvements.

## Priority 1 — Reliability and operations

- Add a container healthcheck endpoint.
- Add structured status details for renderer FPS, FFmpeg health, segment cadence, and buffer readiness.
- Add one-click diagnostic bundle export.
- Add log retention controls so `app_events` cannot grow indefinitely.
- Add explicit startup mode: standby only, auto-start, or restore previous state.

## Priority 2 — Security

- Add authentication.
- Add CSRF protection for admin POST actions.
- Add optional reverse-proxy trusted header support.
- Add upload and admin action audit details.
- Document safe reverse-proxy examples.

## Priority 3 — Renderer evolution

- Replace or augment Pillow renderer with SDL/C/Rust/GPU-capable renderer if performance demands it.
- Add logo rendering from M3U `tvg-logo`.
- Add richer animation profiles.
- Add per-theme layout overrides.
- Add preview snapshots or generated stills in the admin UI.

## Priority 4 — Input handling

- Add M3U/XMLTV validation before saving config.
- Add support for gzipped XMLTV sources.
- Add better timezone controls.
- Add channel mapping diagnostics when M3U IDs and XMLTV IDs do not match.
- Add source refresh scheduling controls.

## Priority 5 — HLS and playback

- Add optional multi-variant HLS output.
- Add configurable bitrate and encoder preset controls.
- Add better player compatibility diagnostics.
- Add stale segment and playlist watchdogs.
- Add safer fallback behavior when live output crashes after clients are already live.

## Priority 6 — Packaging

- Publish versioned container images.
- Add GHCR/Docker Hub release workflow.
- Add TrueNAS custom app guidance.
- Add systemd service example for non-container installs.
- Add backup/restore docs for `data/`.
