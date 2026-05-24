# FAQ

## Is RetroStation MC an IPTV provider?

No. It does not provide IPTV content. It generates a virtual guide channel from M3U/XMLTV metadata that you configure.

## Does it restream the channels from my M3U?

No. v1.0.0 uses the M3U and XMLTV data to render the guide video. The generated output is the guide channel itself.

## What URL should I add to RetroIPTVGuide?

Use:

```text
http://YOUR_SERVER:8787/channel.m3u
```

## Why not use `/hls/master.m3u8` directly as the source?

`/hls/master.m3u8` is the stream URL. `/channel.m3u` is the IPTV playlist wrapper that defines the single virtual channel and points to the stream.

## Why does the HLS stream include audio?

Some IPTV players do not reliably play video-only MPEG-TS HLS. RetroStation MC includes silent AAC audio by default when no music is selected.

## Why does the stream show standby first?

The live guide is not served until enough HLS buffer exists. This avoids handing clients a partial live playlist during startup.

## Why is 15 FPS the default?

A TV guide grid does not require cinematic frame rate. Lower FPS reduces renderer and encoder pressure and improves stability on modest hardware.

## Can I expose this on the internet?

No, not directly. v1.0.0 has no authentication. Use LAN-only access, VPN, or an authenticated reverse proxy.

## Can I add my own themes?

Yes. Add a directory under `app/themes/` with a `theme.json` file.

## Can I use 1080p?

Yes, the admin UI exposes `1920x1080`. Use it only if the host can keep up with rendering and encoding.

## Where are settings stored?

`data/config.db`

## Where are generated HLS files stored?

`output/`

## Where are uploaded music files stored?

`data/music/`
