# RetroIPTVGuide Integration

RetroStation MC is intended to be imported into RetroIPTVGuide as a single virtual channel.

## Recommended source URL

Add this URL as the tuner/source in RetroIPTVGuide:

```text
http://YOUR_SERVER:8787/channel.m3u
```

Do not import `/hls/live.m3u8`, `/hls/standby.m3u8`, or individual HLS segment URLs as tuner sources. Use the single-channel playlist.

## What `/channel.m3u` contains

The generated M3U contains exactly one channel entry:

- `tvg-id="retro-guide-channel"`
- `tvg-name` from the configured title
- `tvg-chno="1"`
- `group-title="Virtual Channels"`
- stream URL pointing to `/hls/master.m3u8`
- XMLTV URL advertised through `url-tvg` and `x-tvg-url`

## XMLTV output

RetroStation MC publishes a simple XMLTV file at:

```text
http://YOUR_SERVER:8787/channel.xmltv
```

The output contains one channel and four-hour programme blocks covering seven days. This creates a readable EPG entry in IPTV clients and represents the guide channel as a continuous virtual channel.

## HLS stream path

The stream path in the M3U is:

```text
http://YOUR_SERVER:8787/hls/master.m3u8
```

The master playlist dynamically points to either:

- `/hls/standby.m3u8` while the guide is stopped or warming up
- `/hls/live.m3u8` once enough live HLS buffer exists

## Why the master playlist matters

The master playlist gives the app a stable public stream URL while still allowing the backend to switch between standby and live media playlists. This avoids exposing clients directly to partial HLS output during startup.

## Audio compatibility

The HLS output includes AAC audio even when no music is selected. In no-music mode, FFmpeg generates a silent stereo AAC track. Some IPTV players will not start video-only MPEG-TS HLS streams reliably; the silent audio track improves compatibility.

## Recommended RetroIPTVGuide workflow

1. Start RetroStation MC.
2. Open the admin UI.
3. Configure playlist/XMLTV sources, title, theme, and render settings.
4. Click **Save & Start**.
5. Confirm the preview plays standby, then live guide content.
6. In RetroIPTVGuide, add `http://YOUR_SERVER:8787/channel.m3u` as a source.
7. Refresh/import sources in RetroIPTVGuide.
8. Tune the imported virtual channel.

## Network notes

Use an address reachable from the RetroIPTVGuide host or browser. For container deployments, `localhost` only works from the same machine. Other devices should use the server hostname or LAN IP.
