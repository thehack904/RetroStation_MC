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

## XMLTV data appears empty

Check that XMLTV channel IDs match M3U `tvg-id` values. RetroStation MC groups programmes by XMLTV `programme@channel` and matches them to parsed channel IDs.

If no programmes overlap the visible time window, the guide will show `No guide data`.

## Channel group filter hides everything

`channel_group` is an exact match against M3U `group-title`. Clear the field to show all channels, or verify spelling and capitalization.

## Playback catches up and spins

Increase the live-edge/buffer protection in Diagnostics:

- Increase Delay Segments from `2` to `3` or `4`.
- Increase Minimum Buffer Seconds from `18` to `24` or `30`.
- Keep segment duration at `6` seconds or higher for stability.
- Keep FPS at `15` unless hardware headroom is confirmed.

Then restart the guide.

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
