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

## [v1.4.0] - 2026-09-17

- Added automatic Guide Preview input transport detection for HLS, MPEG-TS, and local-file preview sources before Guide startup.
- Added source-keyed transport caching through `guide_preview_detected_transport` and `guide_preview_transport_source_key` so a cached result is reused only for the exact selected preview source.
- Added `POST /guide-preview/detect-transport` for bounded selection-time probing of HTTP/HTTPS preview URLs, with server-side fallback detection when settings are saved.
- Added a dedicated MPEG-TS Guide Preview relay that normalizes the selected live MPEG-TS source into a short local HLS relay before FFmpeg overlays it into the Guide.
- Split Guide Preview processing by detected input transport: MPEG-TS uses the dedicated relay/overlay path, while HLS, local files, and unknown network inputs use the shared normalized-frame path.
- Updated Preview-audio synchronization so the HLS/local-file path derives normalized video and optional AAC audio relays from one FFmpeg input clock; the MPEG-TS path maps preview audio from the same relayed source used for video.
- Added transport-specific source pacing: detected HLS inputs use realtime pacing, local files loop in realtime, and live network MPEG-TS inputs avoid the HLS pacing behavior.
- Removed the misleading Guide **Channel Group** filter from the admin form; M3U `group-title` remains parsed channel metadata and is not used to filter Guide rendering.
- Corrected Guide Message parsing/documentation so rotating items use explicit multiline `[message]` ... `[/message]` blocks. Blank lines inside a message are preserved as spacing rather than splitting messages.
- Added timed Guide Message blocks (`[message:seconds]`) and blank intervals (`[blank]`, `[blank:seconds]`), with explicit durations capped at one hour.
- Added regression coverage in `tests/test_guide_preview_transport.py`, `tests/test_guide_preview_integration.py`, and `tests/test_guide_message_live_update.py` for transport detection/caching, pipeline routing, and the corrected message syntax.
- This change concerns **Guide Preview input detection and processing only**. Selectable HLS versus MPEG-TS Guide/Virtual Channel **output** remains a separate future feature request and is not implemented here.

### Hardware acceleration reliability

- Fixed VA-API live encoding for software-rendered RGB24 Guide, Weather, Simulated Traffic, and News Now frames by explicitly opening the DRM render node and converting/uploading frames with `format=nv12,hwupload` before `h264_vaapi`.
- Fixed Guide Preview + VA-API by appending the hardware upload stage to the preview `filter_complex` output instead of bypassing the overlay graph.
- Fixed hardware-encoder fallback loops: a hardware process that exits before producing HLS segments now counts as an initialization failure even when the manager watchdog notices it after the original quick-failure timer. After the configured threshold, the channel uses `libx264` for the remainder of the process session.
- Added equivalent hardware-failure fallback handling to News Now and improved exited child-process reaping.
- Linux install/upgrade now adds the RSMC service account to existing `video` and `render` groups so Intel VA-API/QSV can access `/dev/dri` without a manual `usermod`.
- Added `RSMC_VAAPI_DEVICE` as an optional environment override; the default render node remains `/dev/dri/renderD128`.

### Secondary Guide output

- Added an optional second Guide Channel output controlled from **Guide Channel Render Settings**.
- Secondary output uses its own native resolution-aware renderer rather than scaling the completed primary stream, preserving correct SD/CRT Guide geometry and readable UI proportions.
- Added secondary resolutions `720x480` (default), `640x480`, and `960x720`.
- Secondary rendering reuses the primary Guide data, theme, Guide Message, preview source, timing, and existing hardware/software encoder-selection policy with software fallback.
- Added SD-specific UI scaling for fonts, row heights, header/footer, channel column, clock, padding, and preview geometry.
- Added conservative secondary-output bitrate caps to improve playback stability on lower-power IPTV clients.
- The normal `/channel.m3u` and XMLTV exports automatically include the secondary Guide as a separate selectable channel when enabled; users do not need to configure the raw secondary HLS playlist directly.
- Fixed secondary Guide startup ordering, FFmpeg scale syntax, native preview-overlay alignment, and pipeline status/error reporting.
- Moved **Guide Message** into its own collapsed accordion on the Guide Preview page so its live-save control is visually separated from Preview save/restart controls.

### Guide Channel video preview

- Added an optional classic cable-guide video preview window to the Guide Channel, disabled by default.
- Added Web UI controls for enabling/disabling preview video, choosing an uploaded local file or HTTP/HTTPS/HLS source, uploading/removing local preview video, and selecting audio behavior.
- Added three preview audio modes: **Guide** (existing Guide Channel music), **Preview** (preview-source audio), and **Silent**.
- Added resolution- and aspect-ratio-aware preview layout for all four Guide Channel output profiles: 1280x720 and 1920x1080 widescreen, plus 960x720 and 1440x1080 standard 4:3.
- The renderer now reserves the themed header/information area natively and starts the timeline/listings below the preview region instead of vertically squashing the completed guide frame.
- Preview-enabled pagination automatically reduces visible rows when necessary so lower-resolution layouts do not clip or skip channels.
- Guide Preview Video Source now includes enabled RSMC Virtual Channels (Weather, Simulated Traffic, News Now, and Channel Mix) as directly selectable live preview sources.
- Changed the Guide preview presentation to a raised/beveled video window, visually centered between the bottom of the clock text and the guide grid, with the frame right edge aligned to the clock seconds field across all four supported output resolutions; the shadow now adapts to the active theme header color.
- FFmpeg overlays the preview source inside the renderer-owned themed region while retaining the existing single-renderer Guide Channel pipeline.
- Added Guide Preview aspect controls: **Auto**, **16:9**, and **4:3**. Auto starts immediately with a 16:9 fallback, detects the selected source display aspect ratio asynchronously with a bounded ffprobe worker, caches the result for that exact source, and never blocks Guide startup. Manual 16:9/4:3 overrides skip detection.
- The raised preview frame now changes between 16:9 and 4:3 while preserving its approved vertical center and fixed right-edge clock alignment; only the left edge moves when a 4:3 window is used.
- Added optional rotating **Guide Message** text opposite the video preview. Admin-entered messages support explicit multiline `[message]` blocks, a configurable 3–60 second default interval, automatic wrapping/clipping, and continuous looping; blank lines inside a message are preserved as spacing. Only one understated message is shown at a time; the text is aligned beneath the Guide Channel title, constrained to the left information area, and never displays timing/status controls on the broadcast output.
- Added Guide Message blank-screen directives: **`[blank]`** holds the message area empty for the normal Display Time, while **`[blank:seconds]`** (for example, `[blank:90]`) holds it empty for an explicit duration up to one hour. Saving changed message text/timing restarts the message rotation at the first slide without restarting the Guide pipeline.

### Shared virtual-channel music library and HDHomeRun UI pause

- Temporarily hid and disabled the HDHomeRun/Plex export controls while retaining the backend implementation for later work. Normal RSMC HLS outputs and M3U/XMLTV exports remain available for RetroStation Player, RetroIPTVGuide, TiViMate, VLC, and other IPTV clients.
- Consolidated virtual-channel background audio around the central `data/music/` shared library. Existing legacy Weather music files are copied into the shared library on startup when needed.
- Guide, Weather, Simulated Traffic, and News Now can independently select silence, one track, selected tracks, or all files from the shared library, with independent loop settings.
- Added AAC/silent-audio output to Traffic and News HLS so those channels can use shared background music natively. Channel Mix now has its own shared-library audio override that drops member audio and keeps Mix audio independent of source changes.

### Simulated Traffic broadcast rendering and Virtual Channels export

### News Now virtual channel migration

- Migrated RetroIPTVGuide News Now RSS/Atom behavior into RSMC as CH 4 with a native generated HLS pipeline.
- Added up to six configurable feeds, synchronized 30-minute wall-clock feed rotation, parser normalization, cached/rate-limited polling, safe text rendering, and graceful empty/error states.
- Added independent News HD/SD aspect-ratio and resolution settings, `/api/news`, `/news` preview, `/hls/news.m3u8`, M3U/XMLTV, and HDHomeRun export support.

- Fixed the Simulated Traffic HLS renderer to use the same OpenStreetMap basemap and real road GeoJSON used by the working RetroIPTVGuide traffic display instead of drawing schematic placeholder lines.
- Bundled the ten seed-city basemaps and road GeoJSON datasets from the released RetroIPTVGuide implementation so Traffic works immediately without requiring first-run map downloads.
- Kept congestion levels and incidents explicitly simulated while applying those synthetic conditions to real road geometry.
- Changed the main-page Weather Channel export checkbox to a Virtual Channels master checkbox; enabling it includes Guide, Weather, and Simulated Traffic in RSMC M3U/XMLTV output and enables both optional virtual-channel pipelines.
- Traffic continues to support independent 16:9/4:3 and HD/SD resolution selection.

### HDHomeRun export and channel icon compatibility
- Changed HDHomeRun export to include RSMC-owned channels by default (Guide Channel plus enabled virtual/integration channels).
- Added opt-in per-channel rebroadcast selection for imported source-playlist channels instead of mirroring the entire upstream playlist.
- Added XMLTV `<icon>` metadata for virtual channels.
- Added a PNG Weather Channel icon and prefer it over SVG for broader IPTV-client compatibility, including clients that do not render SVG `tvg-logo` images.

- Added a Channel Mix audio override using the shared music library; member-channel audio is dropped so Mix music remains continuous across source changes.

- Fixed Shared Music uploads after the central-library migration: uploads now return to the Shared Music tab, enforce the documented 100 MB limit per file rather than per multipart request, recreate the music directory when needed, and validate audio headers without reading whole files into memory.

### Fixed

- Fixed a Plex/HDHomeRun file-descriptor exhaustion regression that could leave RetroStation MC running but make the Flask UI and SQLite event store inaccessible after several minutes of tuner playback. RSMC-owned tuner channels now remux directly from local HLS playlists instead of recursively fetching their own HLS over Flask, Channel Mix is materialized to a local continuously refreshed playlist, and every SQLite connection is explicitly closed after use.


### Virtual Channels UI / export controls

- Decoupled optional virtual-channel M3U/XMLTV export from per-channel enablement. The main admin checkbox now includes only channels individually enabled on the Virtual Channels page and no longer enables or disables Weather, Traffic, News Now, or Channel Mix.
- Standardized Weather (CH 2), Simulated Traffic (CH 3), News Now (CH 4), and Channel Mix (CH 5) card headers.
- Added collapsed-by-default accordion controls to all virtual-channel cards.
- Moved Channel Mix into the same constrained Virtual Channels page layout as the other generated channels.


- News Now HLS renderer now mirrors the browser preview layout and caches feed artwork asynchronously for the generated video channel.
- Fixed HDHomeRun playback in Plex by serving tuner URLs as continuous MPEG-TS streams instead of HTTP redirects to HLS playlists. The tuner path remuxes with FFmpeg stream copy (`-c copy`) and does not add a second video transcode.
- Added an HDHomeRun-specific XMLTV endpoint (`/hdhr/guide.xml`, alias `/hdhr/xmltv.xml`) that mirrors the effective tuner lineup and carries through upstream programme data for selected rebroadcast channels.
- Added a simulated Traffic virtual channel (CH 3) migrated and adapted from RetroIPTVGuide v4.9.9-dev. The channel generates synthetic congestion levels and incidents using real OSM road geometry; all traffic conditions are simulated and explicitly labeled as such in the UI.

### Added
- Migrated RetroIPTVGuide Channel Mix as RSMC CH 5 with deterministic wall-clock source rotation, ordered per-channel durations, disabled-source fallback, and HLS-level source switching that reuses existing virtual-channel playout.

- Added native HDHomeRun tuner discovery on UDP port `65001` when HDHomeRun Mode is enabled. Discovery replies advertise the tuner DeviceID, tuner count, HTTP BaseURL, and LineupURL for Plex and other libhdhomerun-compatible clients.
- Added deterministic migration from the previous UUID-style HDHomeRun identifier to a persistent checksum-valid 8-character HDHomeRun DeviceID.
- Added `app/traffic_channel.py` — standalone simulated traffic channel engine. Provides deterministic per-time-slot congestion distributions, synthetic incident generation, city rotation, and OSM road geometry / basemap caching. Adapted from RetroIPTVGuide `app.py` (traffic demo section); RSMC-native configuration storage via `ConfigStore`; no DB or Flask dependency within the module itself.
- Added `app/templates/traffic.html` — retro-styled virtual traffic channel display page with CRT scanline effect, road overlay canvas, incident log, congestion legend, and simulated-only disclaimer.
- Added `/traffic` display page, `/api/traffic` JSON API, `/api/traffic/roads/<city_id>` road GeoJSON API, and `/traffic-map/<filename>` basemap image endpoint.
- Added `/virtual-channels/traffic/config` admin endpoint and Virtual Channels admin card for the simulated traffic channel (city enable/disable, rotation interval, pack size).
- Added `scripts/download_road_data.py` — CLI helper to pre-download Overpass road GeoJSON to `data/roads/` for offline use.
- Added `scripts/generate_basemaps.py` — CLI helper to stitch OSM tile images into 1280×720 basemap PNGs at `data/maps/traffic/`.
- Added `tests/test_traffic_demo.py` — test suite covering city seed data, congestion model, payload builder, incident generation, road cache, basemap generation, and all traffic web routes.

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
- Extended live wall-clock displays to Simulated Traffic and News Now virtual-channel video output, using the configured display/browser timezone.
- Reordered Channel Mix duration controls so the numeric value is followed by the `Minutes` unit before the move controls.
- Updated Pillow from the legacy `10.4.0` pin to `Pillow>=12.0.0,<13` for supported Python 3.11-3.14 environments.
- Linux installation now selects the newest installed, explicitly supported Python 3.11-3.14 interpreter instead of blindly using the system `python3`.
- Python dependency installation now uses binary wheels only, preventing unexpected native source builds during normal installation.

### Fixed
- Added Plex troubleshooting documentation noting that **Disable video stream transcoding** must remain unchecked for Live TV playback through the RSMC HDHomeRun tuner.
- Renamed the built-in Guide and Weather XMLTV/M3U channel IDs from the old `retro-*` format to clearer `rsmc-*` identifiers so Plex channel mapping does not show the legacy `retro-guide-channel` style label.
- Fixed HDHomeRun Mode being unreachable from Plex automatic or targeted discovery because RSMC previously exposed only HTTP lineup endpoints and did not answer the native HDHomeRun UDP discovery protocol.
- Fixed `/discover.json` to advertise `BaseURL` and a libhdhomerun-valid DeviceID rather than a UUID.
- Docker Compose now publishes UDP `65001` in addition to TCP `8787` so HDHomeRun discovery can reach the container.
- Fixed Linux installation failures on Python 3.14 caused by the legacy Pillow 10.4.0 dependency falling back to an unsupported source build.
- Added an explicit future-Python safety check so an unvalidated Python release fails early with an actionable message rather than failing deep inside dependency compilation.

### Security
- (empty)

### Known Issues
- (empty)

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
    `RETROGUIDE_HOST_ALIASES=media.lan=<media-server-ip>,epg.lan=<epg-server-ip>`

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
