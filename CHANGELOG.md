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

## [v1.4.0] - 2026-07-15

### Added

- Added `app/playout_schema.py` for validating playout document JSON.
- Added playout document support for the following item types:
  - `video`
  - `promo`
  - `virtual_channel`
  - `preview_channel`
  - `standby`
- Added playout document validation for required top-level fields:
  - `channel`
  - `items`
- Added item-level validation for required fields:
  - `type`
  - `source`
  - `duration`
- Added positive-duration validation for playout items.
- Added `parse_playout_document()` for loading playout documents from a file path or raw JSON string.
- Added `parse_playout_items()` for returning a deep copy of validated playout items.
- Added `app/playout_scheduler.py` for stepping through validated playout documents.
- Added scheduler state output for active item, next item, item elapsed time, item remaining time, document duration, active index, next index, and loop cycle.
- Added looping and non-looping scheduler modes.
- Added `app/playout_fallback.py` for standby video and fallback playout handling.
- Added fallback behavior for missing `video` and `promo` source files.
- Added fallback behavior for blank `virtual_channel` and `preview_channel` source names.
- Added `PlayoutFallbackEvent` diagnostics with JSON-serializable output.
- Added warning-level fallback logging under the `playout_fallback` category.
- Added `sample_data/playout_example.json` as a working playout document example.
- Added `docs/PLAYOUT_DOCUMENT.md` documenting the playout schema, item types, validation rules, and parser helpers.
- Added unified Linux helper script `retrostation_linux.sh` with `install` and `uninstall` commands.
- Added regression tests for playout schema validation, scheduler behavior, fallback behavior, and the unified Linux helper script.

* Added `app/playout_fallback.py` — standby video and fallback playout handling.

  * `PlayoutFallbackHandler` inspects each scheduled playout item before it reaches
    the renderer and replaces unavailable items with appropriate standby fallbacks.
  * `video` and `promo` items whose source file does not exist on disk are replaced
    by `FALLBACK_VIDEO_ITEM` (type `standby`, source `default`, duration 60 s).
  * `virtual_channel` and `preview_channel` items whose source name is empty or
    blank are replaced by `FALLBACK_VIRTUAL_CHANNEL_ITEM`.
  * `standby` items are always passed through unchanged; they represent the
    fallback content itself.
  * Every fallback trigger is logged at WARNING level under the
    `playout_fallback` category with the item type, source, reason, and a
    human-readable detail string so administrators can identify why fallback
    was activated.
  * The most recent fallback event is exposed as `handler.last_fallback_event`
    (a `PlayoutFallbackEvent` with a `.to_dict()` method) for admin diagnostics.
  * The availability check and fallback item definitions are injectable, making
    the handler straightforward to test and extend.

### Changed
- Updated repository version references from `v1.3.0` to `v1.4.0`.
- Updated the admin About tab version from `v1.3.0` to `v1.4.0`.
- Updated README Linux install command to use `sudo ./retrostation_linux.sh install`.
- Updated README Linux uninstall command to use `sudo ./retrostation_linux.sh uninstall`.
- Updated installation documentation to use the unified Linux helper script.
- Updated the copy URL button styling to use theme button colors.
- Updated the additional roadmap to mark v1.4.0 fallback behavior tasks and acceptance criteria as complete.

### Fixed
- Fixed scheduled playout handling so unavailable media items can be routed to standby instead of stopping the channel path.
- Fixed fallback diagnostics so administrators can inspect the most recent fallback reason.
- Fixed Linux installer tests to validate the unified install/uninstall script.
- Fixed admin About section tests to expect `v1.4.0`.

### Removed
- Removed `install-linux.sh`.
- Removed `uninstall-linux.sh`.
- Replaced both scripts with `retrostation_linux.sh`.

### Security
- (empty)

### Known Issues
- Playout documents are schema-validated and schedulable, but they are not yet exposed through a full admin UI.
- The Preview Channel renderer is not yet fully driven by playout scheduler state.

## [v1.3.0] - 2026-07-01

### Added

* Added first-pass hardware acceleration capability detection through `app/gpu_capabilities.py`.
* Added `gpu_hwaccel_detect_v3.py` for local GPU, FFmpeg encoder, Docker visibility, and software-fallback diagnostics.
* Added hardware acceleration mode configuration through `hardware_acceleration_mode`.

  * `software_fallback` keeps encoding on `libx264`.
  * `hardware_if_available` selects encoder-ready hardware only when a functional FFmpeg probe succeeds.
* Added admin hardware acceleration controls and status reporting for:

  * detected hardware devices
  * Docker-visible hardware devices
  * FFmpeg-reported encoders
  * encoder-ready providers
  * selected encode path
  * software fallback status
* Added functional FFmpeg hardware encoder probes so detected GPUs are not treated as usable unless the selected encoder can successfully encode.
* Added the dedicated Weather virtual channel.

  * Added Virtual Channels admin page at `/virtual-channels`.
  * Added Weather Channel browser preview page at `/weather`.
  * Added dedicated Weather HLS playlist at `/hls/weather.m3u8`.
  * Added Weather renderer support through `app/weather_renderer.py`.
  * Added Weather radar support through `app/weather_radar.py`.
  * Added Weather Channel state tracking through `data/weather_state.json`.
* Added Weather Channel configuration options:

  * enable/disable Weather Channel
  * latitude and longitude
  * location name
  * temperature units
  * segment duration
  * background condition override
  * Weather logo export toggle
* Added Weather Channel playlist and EPG export support.

  * `/channel.m3u` and `/channel.m3u8` can now include enabled virtual channels.
  * `/channel.xmltv` can now include XMLTV entries for enabled virtual channels.
* Added Weather Channel logo support.

  * Added bundled default Weather logo at `data/weather_logo/default.svg`.
  * Added Weather logo route at `/weather-logo/<filename>`.
  * Added optional `tvg-logo` metadata for Weather playlist export.
* Added Weather Channel background music support.

  * Added separate Weather music upload/delete workflow.
  * Added separate Weather music library under `data/weather_music/`.
  * Added Weather-specific single-track and playlist modes.
* Added Weather data APIs and supporting integrations:

  * `/api/weather`
  * `/api/weather/bg_override`
  * `/api/weather/zip-lookup`
  * `/weather-radar/current.png`
* Added Open-Meteo forecast integration for current, hourly, and daily Weather Channel data.
* Added NWS active weather alert integration for U.S. locations.
* Added ZIP-code lookup support through Nominatim for easier Weather Channel location setup.
* Added radar image generation and caching support.
* Added `requests>=2.31.0` as a runtime dependency for Weather data, radar, alerts, and ZIP lookup requests.
* Added test coverage for:

  * GPU capability detection
  * hardware encoder fallback
  * hardware acceleration status UI
  * Weather virtual channel exports
  * Weather radar handling
  * Weather rendering
  * M3U parsing

### Changed

* Updated FFmpeg profile resolution so hardware acceleration is selected only when an encoder-ready provider passes validation.
* Updated guide and Weather HLS pipelines to share the same hardware/software encode-path selection logic.
* Updated the admin UI with Virtual Channels navigation, Weather Channel controls, and expanded hardware acceleration status.
* Updated M3U/XMLTV generation to support multiple exported virtual channels instead of only the main guide channel.
* Updated Linux installation diagnostics to run `gpu_hwaccel_detect_v3.py` and report whether hardware encoding is usable or software fallback will be used.
* Updated Docker host-alias examples so the default package no longer includes a private LAN IP address.

  * `RETROGUIDE_HOST_ALIASES` should remain unset by default.
  * Host aliases are now documented as optional deployment-specific overrides, for example:
    `RETROGUIDE_HOST_ALIASES=iptv.lan=<media-server-ip>,epg.lan=<epg-server-ip>`

### Fixed

* Fixed Weather virtual channel playlist entries so they point to `/hls/weather.m3u8` instead of the embedded Weather preview page.
* Improved hardware acceleration fallback behavior so detected-but-unusable GPUs do not cause the pipeline to fail when software fallback is available.
* Removed private-network example IPs from default Docker host-alias examples to avoid interfering with user deployments.

### Security

* Served Weather logo files use sanitized filenames and no-cache headers, matching the guide-logo delivery model.
* Weather music upload/delete handling uses sanitized filenames and validates selections against the uploaded Weather music library.
* Docker host-alias configuration is now opt-in so the default compose example does not expose or assume a private LAN topology.

### Known Issues

* RetroStation MC v1.3.0 still has no built-in authentication. Keep it LAN-only, behind a VPN, or behind an authenticated reverse proxy.
* Weather Channel output depends on external data providers for forecast, alert, radar, and ZIP lookup data. If those services are unavailable, Weather output may fall back to partial or placeholder data.
* Hardware acceleration support depends on the host GPU, Docker runtime visibility, installed drivers, and FFmpeg encoder support. If hardware encoding is unavailable or fails validation, RetroStation MC falls back to software encoding.

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
- Added `uninstall-linux.sh` (later unified into `retrostation_linux.sh`).
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
- Reworked `install-linux.sh` into a root/systemd installer (later unified into `retrostation_linux.sh`) that:
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
