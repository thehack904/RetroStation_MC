# Changelog

## v1.0.0

Initial RetroStation MC release.

### Added

- Flask admin dashboard.
- SQLite-backed configuration store.
- SQLite-backed application event log.
- M3U parser for channel metadata.
- XMLTV parser for programme metadata.
- Normalized `guide_state.json` generation.
- Pillow-based headless renderer.
- FFmpeg HLS encoding pipeline.
- HLS master playlist endpoint.
- Standby HLS segment and standby media playlist.
- Live media playlist endpoint with delayed live-edge trimming.
- Backward-compatible `/hls/guide.m3u8` endpoint.
- Single-channel M3U output endpoint.
- Single-channel XMLTV output endpoint.
- Admin preview using hls.js.
- Stream versioning for standby/live transitions.
- Background music upload and selection.
- Silent AAC fallback audio.
- Diagnostics settings for HLS buffer and live-edge behavior.
- JSONL and CSV log export.
- Dockerfile and Docker Compose support.
- Bundled sample M3U and XMLTV data.
- Multiple bundled JSON themes.

### Notes

- No authentication is included in v1.0.0.
- The app should be deployed local-only or behind external access controls.
- The renderer is intentionally implemented in Python/Pillow for rapid validation and future replacement.
