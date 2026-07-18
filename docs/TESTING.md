# Testing Guide

The repository includes tests for admin UI behavior, HLS live-edge behavior, renderer caching, and default stream tuning.

## Run tests

```bash
pip install pytest
pytest
```

## Existing test focus

| Test file | Focus |
|---|---|
| `tests/test_admin_about_section.py` | About section/version content |
| `tests/test_admin_copy_url_helper.py` | Copy URL helper behavior in admin HTML |
| `tests/test_default_stream_tuning.py` | Default HLS/render tuning expectations |
| `tests/test_hls_delayed_edge.py` | HLS live-edge playlist trimming |
| `tests/test_renderer_layer_cache.py` | Renderer layer cache behavior |
| `tests/test_playout_schema.py` | Playout document schema validation and parser behavior |
| `tests/test_playout_scheduler.py` | Active/next item scheduling, looping, non-looping behavior, and timing state |
| `tests/test_playout_fallback.py` | Missing media fallback, virtual/preview source fallback, diagnostics, and scheduler integration |
| `tests/test_linux_installer.py` | Unified Linux install/uninstall script expectations |

## Manual regression checklist

Before release, validate:

1. Docker Compose starts cleanly.
2. Admin UI opens on port `8787`.
3. `/channel.m3u` returns the guide channel plus any enabled virtual channels.
4. `/channel.xmltv` returns valid XML for the guide channel plus any enabled virtual channels.
5. `/hls/master.m3u8` returns standby before guide start.
6. **Save & Start** starts renderer and FFmpeg.
7. `output/guide.m3u8` and `output/guide_*.ts` are created.
8. `/status` eventually reports `guide_buffered: true`.
9. `/hls/master.m3u8` switches from standby to live variant.
10. Admin preview reloads after standby/live transition.
11. Stop Guide returns stream to standby.
12. Restart Guide uses increasing HLS media sequence values.
13. Logs export works in JSONL and CSV.
14. Uploaded supported music can be selected and applied after restart.
15. Unsupported or invalid audio uploads are rejected.
16. `sudo ./retrostation_linux.sh install` installs the systemd service on a Linux/systemd host.
17. `sudo ./retrostation_linux.sh uninstall` removes the service and install directory safely.
18. `sample_data/playout_example.json` validates successfully through `app.playout_schema.parse_playout_document`.
19. Missing video or promo items route to standby through `PlayoutFallbackHandler`.
20. Blank virtual/preview channel sources route to standby through `PlayoutFallbackHandler`.

## HLS-specific regression checks

Use curl while the pipeline is warming up:

```bash
curl http://localhost:8787/hls/master.m3u8
curl http://localhost:8787/hls/standby.m3u8
curl http://localhost:8787/hls/live.m3u8
```

Expected behavior:

- Standby playlist returns 200 before live is ready.
- Live playlist returns 404 before live is ready.
- After buffering, standby returns 404 and live returns 200.

## Playback checks

Test at least:

- Browser admin preview
- VLC or another standalone HLS player
- RetroIPTVGuide import via `/channel.m3u`

Prefer testing from another LAN device to catch hostname, container port, and CORS issues.
