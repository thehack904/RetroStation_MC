# FAQ

## Is RetroStation MC an IPTV provider?

No. It does not provide IPTV content. It generates a virtual guide channel from M3U/XMLTV metadata that you configure.

## Does it restream the channels from my M3U?

No. RetroStation MC uses the M3U and XMLTV data to render the guide video. The generated output is the guide channel itself.

## What URL should I add to RetroIPTVGuide?

Use:

```text
http://YOUR_SERVER:8787/channel.m3u
```

## Why not use `/hls/master.m3u8` directly as the source?

`/hls/master.m3u8` is the guide stream URL. `/channel.m3u` is the IPTV playlist wrapper that defines the guide channel and any enabled virtual channels.

## Why does the HLS stream include audio?

Some IPTV players do not reliably play video-only MPEG-TS HLS. RetroStation MC includes silent AAC audio by default when no music is selected.

## Why does the stream show standby first?

The live guide is not served until enough HLS buffer exists. This avoids handing clients a partial live playlist during startup.

## Why is 15 FPS the default?

A TV guide grid does not require cinematic frame rate. Lower FPS reduces renderer and encoder pressure and improves stability on modest hardware.

## Can I expose this on the internet?

No, not directly. v1.4.0 still has no authentication. Use LAN-only access, VPN, or an authenticated reverse proxy.

## Can I export more than one virtual channel?

Yes. `/channel.m3u`, `/channel.m3u8`, and `/channel.xmltv` always include the guide channel and can also include the Weather Channel when it is enabled.

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

## What is a playout document?

A playout document is a JSON schedule that describes a sequence of content items for a channel. v1.4.0 supports the base schema and scheduler for video, promo, virtual channel, preview channel, and standby items.

## Does v1.4.0 make the Preview Channel fully scheduler-driven?

Not yet. v1.4.0 adds the schema, scheduler, and fallback foundation. Full scheduler-to-renderer integration remains future work.

## What happens if a scheduled item is missing?

The fallback handler replaces unavailable items with standby content. Missing `video` and `promo` files, or blank virtual/preview channel source names, trigger fallback behavior and are logged under the `playout_fallback` category.
