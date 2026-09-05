# Troubleshooting

## Admin page opens but guide does not play

Check status:

```bash
curl http://localhost:8787/status
```

Look at:

- `pipeline_active`
- `renderer_running`
- `ffmpeg_running`
- `guide_buffered`
- `last_refresh_status`

If `pipeline_active` is false, open the admin UI and click **Save & Start**.

## Stream stays on standby

Possible causes:

1. The guide pipeline was never started.
2. FFmpeg failed to produce `output/guide.m3u8`.
3. Not enough live segments exist yet.
4. Minimum buffer seconds have not elapsed.
5. The renderer or FFmpeg process crashed.

Checks:

```bash
ls -lah output/
curl http://localhost:8787/status
curl http://localhost:8787/logs?limit=50
```

## Preview works but external client does not

Use the correct URL from the client device:

```text
http://SERVER_LAN_IP:8787/channel.m3u
```

Do not use `localhost` unless the IPTV client is running on the same host.

Also verify the client can fetch:

```text
http://SERVER_LAN_IP:8787/hls/master.m3u8
http://SERVER_LAN_IP:8787/channel.xmltv
```

## RetroIPTVGuide imports HLS segments as channels

Use `/channel.m3u`, not `/hls/master.m3u8`, as the source playlist.

Correct:

```text
http://YOUR_SERVER:8787/channel.m3u
```

Incorrect as a tuner playlist:

```text
http://YOUR_SERVER:8787/hls/master.m3u8
```

## FFmpeg fails to start

Verify FFmpeg is installed:

```bash
ffmpeg -version
```

In Docker, FFmpeg is installed by the Dockerfile. For local Python, install it through your OS package manager.

If you expected hardware encoding, also check the **Hardware Acceleration** tab. A detected GPU is not enough by itself; the provider must also be encoder-ready before the app will leave software fallback.

## XMLTV data appears empty

Check that XMLTV channel IDs match M3U `tvg-id` values. RetroStation MC groups programmes by XMLTV `programme@channel` and matches them to parsed channel IDs.

If no programmes overlap the visible time window, the guide will show `No guide data`.

## Hostname-based tuner or EPG URLs fail in Docker

When running in Docker, LAN hostnames may fail even if they resolve on your host machine. For example, `http://media.lan:8409/iptv/channels.m3u` might fail while `http://192.168.50.25:8409/iptv/channels.m3u` works.

Try one of these:

1. Use the source IP address directly.
2. Configure container DNS.
3. Add Docker `extra_hosts`.
4. Set `RETROGUIDE_HOST_ALIASES`.

Example:

```bash
RETROGUIDE_HOST_ALIASES=media.lan=192.168.50.25
```

After aliasing, this source remains valid in app configuration:

```text
http://media.lan:8409/iptv/channels.m3u
```

## Channel group filter hides everything

`channel_group` is an exact match against M3U `group-title`. Clear the field to show all channels, or verify spelling and capitalization.

## Playback catches up and spins

Increase the live-edge/buffer protection in Diagnostics:

- Increase Delay Segments from `2` to `3` or `4`.
- Increase Minimum Buffer Seconds from `18` to `24` or `30`.
- Keep segment duration at `6` seconds or higher for stability.
- Keep FPS at `15` unless hardware headroom is confirmed.

Then restart the guide.

## Hardware acceleration never becomes active

Check all three layers:

1. The main form is set to **Use encoder-ready hardware when available**.
2. The **Hardware Acceleration** tab shows the provider under **Hardware-Ready Providers**, not only **Detected Devices**.
3. Your local install or container exposes the GPU and FFmpeg encoder to the app.

If the provider is detected but not encoder-ready, RetroStation MC will stay on software fallback by design.

## Weather Channel is missing or stays on standby

Confirm:

1. **Weather Channel** is enabled on the main admin page.
2. Weather-specific settings were saved on `/virtual-channels`.
3. The client can fetch `http://YOUR_SERVER:8787/hls/weather.m3u8`.

While the Weather pipeline is warming up, `/hls/weather.m3u8` intentionally falls back to the standby playlist.

## Guide is buffered but viewers still see standby overnight

Check the **Off Air** tab. During the configured off-air window:

- `/hls/master.m3u8` stays on standby
- `/hls/live.m3u8` returns `404`
- the standby variant may use either the normal standby pattern or the static-noise clip, depending on **Play static noise while off-air**

## 1080p stutters on low-power hardware

Use:

- `1280x720`
- `15 FPS`
- `6` second segments
- `visible_rows` between `6` and `10`

The Python/Pillow renderer is not a final high-performance rendering engine.

## Uploaded music does not play

Confirm:

1. The file is in `data/music/`.
2. The file extension is allowed.
3. The file passed magic-byte validation.
4. Music mode is `single` or `playlist`.
5. The guide pipeline was restarted after changing music settings.

## Logs are too short in the UI

Increase **Log Tail Lines** in the Diagnostics tab. The logs database may contain more entries than the UI currently displays.

## Reset configuration

Stop the app, back up the database, then remove it:

```bash
cp data/config.db data/config.db.backup
rm data/config.db
```

Restart the app. It will recreate defaults.

## Clear HLS output

Stop the app or stop the guide, then remove generated HLS files:

```bash
rm -f output/guide.m3u8 output/guide_*.ts
```

Leave `output/standby.ts` in place if you want standby to remain immediately available.


## Plex cannot find HDHomeRun Export

1. Confirm **HDHomeRun Export** is enabled in the RSMC admin page.
2. Confirm the HTTP metadata works:

```bash
curl http://SERVER_IP:8787/discover.json
curl http://SERVER_IP:8787/lineup.json
```

3. Confirm native discovery from the Plex host or another machine on the same subnet:

```bash
hdhomerun_config discover
```

4. Confirm RSMC is listening on UDP 65001:

```bash
ss -lunp | grep 65001
```

5. If a firewall is active, allow UDP 65001 from the LAN. Automatic HDHomeRun discovery is local-subnet broadcast traffic and normally will not cross routed VLAN/subnet boundaries without a broadcast relay.


## HDHomeRun playback fails in Plex

If Plex discovers the RSMC tuner and accepts `/hdhr/guide.xml` but reports that it cannot tune a channel, verify the tuner endpoint directly:

```bash
curl -I http://YOUR_SERVER:8787/hdhr/channel/0
```

It should return `200 OK` with `Content-Type: video/mp2t`. A normal GET is a continuous MPEG-TS stream and will not finish until the client disconnects. RSMC remuxes the existing Guide, Weather, or selected source stream with FFmpeg `-c copy`; it does not add a second video transcode.


## Plex tuner playback shows "Source is unavailable"

If Plex can discover the RSMC HDHomeRun tuner, guide mapping succeeds, and `/hdhr/channel/<n>` plays with `curl`/`ffprobe`, but Plex playback still fails, check **Settings → Server → Transcoder** in Plex. Ensure **Disable video stream transcoding** is **unchecked**. Plex may still require a transcode/remux decision for Live TV playback even when the incoming RSMC tuner stream is already H.264/AAC in MPEG-TS.
