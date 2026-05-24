# RetroStation MC Documentation Index

This documentation pack covers RetroStation MC v1.0.0.

## Operator documentation

- [Installation](INSTALLATION.md) — Docker Compose, local Python, environment variables, persistent paths.
- [Admin User Guide](ADMIN_USER_GUIDE.md) — How to operate the web UI.
- [Configuration Reference](CONFIGURATION.md) — Every major setting and what it changes.
- [Background Music](BACKGROUND_MUSIC.md) — Uploads, single-track mode, playlist mode, looping, and audio behavior.
- [RetroIPTVGuide Integration](RETROIPTVGUIDE_INTEGRATION.md) — How to add the output as a virtual channel.
- [Troubleshooting](TROUBLESHOOTING.md) — Playback, standby, HLS, XMLTV, FFmpeg, and Docker issues.

## Technical documentation

- [Architecture](ARCHITECTURE.md) — Application components and process model.
- [Data Flow](DATA_FLOW.md) — M3U/XMLTV ingestion through HLS output.
- [HLS Pipeline](HLS_PIPELINE.md) — Standby/live playlists, delayed live edge, segment tuning, and client behavior.
- [API Reference](API_REFERENCE.md) — Web endpoints exposed by the Flask app.
- [Renderer](RENDERER.md) — Pillow renderer behavior, paging, caching, and timing.
- [Themes](THEMES.md) — Theme directory layout and JSON color keys.
- [Diagnostics and Logging](DIAGNOSTICS_AND_LOGGING.md) — App events, log export, and telemetry mode.
- [Development Guide](DEVELOPMENT.md) — Local dev, tests, layout, and safe change areas.
- [Testing Guide](TESTING.md) — Existing test focus areas and recommended regression checks.

## Project documentation

- [Security](SECURITY.md) — Local-only threat model and safe deployment guidance.
- [Roadmap](ROADMAP.md) — Suggested post-v1.0.0 work.
- [Changelog](CHANGELOG.md) — v1.0.0 initial release notes.
- [FAQ](FAQ.md) — Common operator and integration questions.
