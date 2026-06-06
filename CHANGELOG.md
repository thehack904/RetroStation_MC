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

## [v1.2.0] - 2026-06-06

### Added

- Added an **Off Air** admin tab for scheduling a daily off-air window.
- Added off-air configuration keys:
  - `off_air_enabled`, `off_air_start`, `off_air_end`, `off_air_static_enabled`
- Added off-air HLS behavior:
  - `/hls/master.m3u8` routes viewers to standby during the off-air window.
  - `/hls/live.m3u8` returns `404` while off-air.
  - `/hls/standby.m3u8` remains available while off-air, even if the live guide pipeline is buffered.
- Added optional **TV static noise** output during off-air windows.
- Added static segment generation as `output/static.ts`.
- Added a **Standby Pattern** admin tab.
- Added standby pattern image upload support.
- Added standby pattern preview, selection, default reset, and removal controls.
- Added support for custom standby pattern formats:
  - PNG, JPG / JPEG, GIF, WEBP, BMP
- Added standby pattern upload validation:
  - sanitized filenames
  - allowed extensions only
  - 10 MB maximum file size
  - image parse/verification check
- Added configurable standby text overlay controls:
  - enable/disable overlay
  - overlay opacity from 0% to 100%
- Added `standby_custom_file`, `standby_overlay_enabled`, and `standby_overlay_opacity` configuration keys.
- Added HLS continuity watchdog diagnostics.
- Added HLS watchdog status display in the **Diagnostics** tab:
  - HLS continuity health
  - playlist age
  - latest segment
  - latest segment age
  - watchdog warnings
- Added watchdog detection for:
  - missing/unreadable playlists
  - stalled playlist updates
  - empty playlist windows
  - missing latest segments
  - stalled segment generation
  - stale playlist windows
- Added throttled watchdog warning logs under `hls.watchdog`.
- Added FFmpeg profile abstraction through new `app/ffmpeg_profiles.py`.
- Added default `software_default` FFmpeg profile.
- Added placeholder hardware acceleration provider definitions for:
  - NVIDIA, Intel, AMD, VAAPI
- Added `ffmpeg_profile` configuration key.
- Added profile-based FFmpeg command generation for video codec, audio codec, resolution, preset, bitrate, and HLS segment length.
- Added `uninstall-linux.sh`.
- Added `docs/ADDITIONAL_ROADMAP.md`.
- Added new test coverage for:
  - FFmpeg profile resolution and command generation
  - HLS watchdog health/degraded states
  - off-air schedule boundary handling
  - standby pattern upload/selection/settings
  - standby overlay behavior
  - Linux installer/uninstaller behavior

### Changed

- Updated the Admin **About** tab version from `v1.1.0` to `v1.2.0`.
- Refactored FFmpeg command construction out of the main pipeline start logic into a dedicated helper.
- Updated audio FFmpeg argument generation to use the selected profile audio codec instead of hardcoding AAC.
- Updated standby generation so a custom uploaded pattern can replace the generated SMPTE-style pattern.
- Updated standby playlist generation so it can serve either `standby.ts` or `static.ts`.
- Updated pipeline start/stop behavior to generate both standby and static segments.
- Updated stale output cleanup to preserve both `standby.ts` and `static.ts`.
- Updated Docker Compose service and container names from `RetroStation_MC` to lowercase `retrostation-mc`.
- Reworked `install-linux.sh` into a root/systemd installer that:
  - requires root/sudo
  - creates/uses an `iptv` system user
  - installs to `/home/iptv/retrostation-mc`
  - creates a Python virtual environment
  - installs requirements
  - creates and starts a `retrostation-mc` systemd service
- Updated README and installation documentation with the new Linux uninstall flow.
- Updated architecture, configuration, HLS pipeline, and admin user documentation for the new off-air, standby pattern, watchdog, and installer behavior.

### Fixed

- Improved HLS observability by surfacing stalled playlist/segment conditions directly in diagnostics.
- Improved standby/live switching behavior during scheduled off-air windows.
- Improved standby pattern robustness by validating uploaded image content before use.
- Improved FFmpeg configurability by centralizing codec/profile settings instead of scattering hardcoded values.

### Security / Hardening

- Added validation and sanitization for standby pattern uploads.
- Added file size limits for standby pattern uploads.
- Added no-cache headers for served standby pattern files.
- Hardened Linux install path handling by refusing unexpected staging locations.
- Added uninstall logic that preserves the `iptv` user when `/home/iptv` still contains other files.

### Known Issues / Notes

- The hardware acceleration providers are currently placeholders; only the default software FFmpeg profile is implemented.

---

## [v1.1.0] - 2026-05-27

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
