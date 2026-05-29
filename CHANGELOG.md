# Changelog

All notable changes to RetroStation MC will be documented in this file.

This project uses a simple release-based changelog format with the following sections when applicable:

- `Added` for new features.
- `Changed` for changes in existing behavior.
- `Fixed` for bug fixes.
- `Removed` for removed files or behavior.
- `Security` for vulnerability or hardening changes.
- `Known Issues` for confirmed limitations that remain open.

---

## [v1.2.0] - Unreleased

### Added
- Off-air schedule feature: admins can configure a daily time window during which the stream shows the standby test pattern (static) instead of the live guide, mimicking classic TV channels that went off air overnight.
- New `off_air_enabled`, `off_air_start`, and `off_air_end` configuration keys.
- New **Off Air** admin tab with enable checkbox, start time, and end time inputs, plus a live status indicator.
- `_is_off_air()` helper used by HLS endpoints to honor the off-air window.
- During the off-air window: `/hls/master.m3u8` routes to the standby variant; `/hls/live.m3u8` returns 404; `/hls/standby.m3u8` always serves even when the guide pipeline is buffered.
- Unit tests for time-string coercion and all off-air boundary conditions.

---

## [v1.1.0] - 2026-05-25 - Beta

### Added
- RetroStation MC as the new default bundled theme.
- Bundled themes `retrostation_mc`, `classic_cable`, and `ersatztv` (displayed as `Icon Guide` in the admin UI).
- Admin UI support for Guide Icon / M3U logo control.
- Default guide logo support served from `data/guide_logo`.
- Custom guide icon upload, preview, removal, and serving through `/guide-logo/<filename>`.
- M3U `tvg-logo` export support for `/channel.m3u` and `/channel.m3u8`.
- Guide logo mode options for the default icon, an uploaded custom icon, or a disabled icon.
- Browser timezone detection so the `local` display mode follows the admin browser's detected IANA timezone.
- Group-specific Movies and Sports programme cell colors when a theme defines `program_bg_movies` or `program_bg_sports`.
- `bump_version.py` helper script for coordinated version updates.
- Regression coverage for bundled themes, guide logo controls, M3U logo output, version bumping, and event storage trimming.

### Changed
- Default theme from `classic_blue` to `retrostation_mc`.
- README version references from v1.0.0 to v1.1.0.
- Admin About tab version from v1.0.0 to v1.1.0.
- Theme selection labels to show friendly names from `theme.json`.
- Guide state output to include timezone and browser timezone metadata.
- Renderer time labels and header clock to respect the configured display timezone.
- Theme documentation to cover the new bundled themes and optional Movies/Sports programme background overrides.
- Configuration documentation to reflect the new default theme.
- Documentation index changelog link to point to the root `CHANGELOG.md`.

### Fixed
- Local timezone display behavior by capturing the browser timezone instead of relying only on the server/container timezone.
- Exported channel playlists so IPTV clients can receive a guide/channel logo through `tvg-logo`.
- Potential unbounded app event database growth by trimming old events when `data/config.db` exceeds 500 MB.
- Custom guide icon handling by validating file extension, size, and image signature before accepting uploads.

### Removed
- Duplicate `docs/CHANGELOG.md`; the changelog is now centralized at the repository root.

### Security
- Guide icon upload hardening with allowed extensions only, a 5 MB maximum file size, basic file signature/content validation, and sanitized filenames via `secure_filename`.
- No-cache headers for served guide logo files.

### Known Issues
- No new known issues were added for v1.1.0.


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
