# Testing Guide

The repository includes tests for admin UI behavior, Guide Preview transport/pipeline behavior, Guide Message parsing/live updates, HLS live-edge behavior, renderer caching, hardware acceleration, virtual channels, and default stream tuning.

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
| `tests/test_guide_preview_transport.py` | HLS/MPEG-TS/file detection, source-keyed cache behavior, endpoint responses, and transport-specific FFmpeg routing |
| `tests/test_guide_preview_integration.py` | Guide Preview source/audio integration |
| `tests/test_guide_message.py` | Guide Message block/timing parser behavior |
| `tests/test_guide_message_live_update.py` | Correct multiline examples, live-save behavior, and removal of the Channel Group control |
| `tests/test_renderer_layer_cache.py` | Renderer layer cache behavior |

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
16. Guide Preview selection detects HLS versus MPEG-TS and does not reuse a cached transport after changing the selected source.
17. HLS/local-file Preview and MPEG-TS Preview each start through their intended processing path, including Preview-audio mode.
18. Guide Message examples render as separate explicit `[message]` blocks; blank lines inside a block remain spacing.

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
