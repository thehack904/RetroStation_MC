# Contributing

## Development baseline

- Keep the public import URL `/channel.m3u` stable.
- Keep the public stream URL `/hls/master.m3u8` stable.
- Preserve local-only security assumptions unless authentication is implemented.
- Add tests for behavior that affects HLS startup, standby/live switching, renderer timing, or admin routes.

## Pull request checklist

- [ ] Tested with `pytest`.
- [ ] Tested admin UI manually.
- [ ] Tested `/channel.m3u` and `/hls/master.m3u8` manually.
- [ ] Updated docs for config/API/behavior changes.
- [ ] Considered playback impact for IPTV clients.
- [ ] Avoided adding public internet exposure assumptions.

## Code style

Prefer clear, explicit Python over clever abstractions. HLS and process lifecycle code should be easy to audit because small changes can affect playback stability.
