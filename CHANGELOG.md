# Changelog

All notable changes to RetroStation MC will be documented in this file.

This project uses a simple release-based changelog format with the following sections when applicable:

- `Added` for new features.
- `Changed` for changes in existing behavior.
- `Fixed` for bug fixes.
- `Security` for vulnerability or hardening changes.
- `Known Issues` for confirmed limitations that remain open.

---

## [v1.0.0] - 2026-05-23

Initial public release of RetroStation MC.

RetroStation MC is a proof-of-concept TV station / master-control style application that renders a retro electronic program guide into an HLS video stream. It is designed to work alongside RetroIPTVGuide or other IPTV clients by exposing M3U and XMLTV-compatible outputs.

### Added

- Flask-based web application for RetroStation MC.
- Browser-based admin dashboard.
- Admin preview player using HLS playback.
- SQLite-backed configuration database.
- SQLite-backed application event log.
- M3U playlist parser for channel metadata.
- XMLTV parser for programme metadata.
- Normalized guide state generation through `guide_state.json`.
- Pillow-based headless guide renderer.
- FFmpeg-based HLS encoding pipeline.
- HLS master playlist endpoint.
- Standby video playlist handling.
- Live guide playlist handling.
- Backward-compatible `/hls/guide.m3u8` endpoint.
- Stream versioning for standby/live transitions.
- Single-channel M3U output endpoint.
- Single-channel XMLTV output endpoint.
- Background music upload support.
- Background music selection and enablement controls.
- Silent AAC fallback audio generation when no background audio is selected.
- Configurable diagnostics and HLS buffering settings.
- Log export in JSONL format.
- Log export in CSV format.
- Bundled sample M3U playlist data.
- Bundled sample XMLTV guide data.
- Bundled JSON-based visual themes.
- Dockerfile for container builds.
- Docker Compose example for local deployment.
- Project documentation set covering installation, configuration, architecture, API behavior, troubleshooting, and development workflow.

### Changed

- Establishes RetroStation MC as the project name for the station-control / guide-rendering application.
- Establishes v1.0.0 as the baseline release for future documentation, issue tracking, and version comparison.

### Known Issues

- No built-in authentication is included in v1.0.0.
- The application should be deployed on a trusted local network or behind external access controls.
- The renderer is implemented in Python/Pillow as a validation-oriented implementation and may be replaced or optimized in later releases.
- HLS timing and live-edge behavior may require tuning depending on host performance, FFmpeg behavior, browser buffering, and client playback behavior.
- Standby/live switching depends on playlist state, segment generation, and client refresh behavior.

### Security

- v1.0.0 should not be exposed directly to the public internet.
- Operators should place the application behind a reverse proxy, VPN, authentication layer, or trusted LAN boundary if remote access is required.

