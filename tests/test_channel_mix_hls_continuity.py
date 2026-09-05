from pathlib import Path

from app.channel_mix import ChannelMixHLSState, parse_media_playlist


def _playlist(prefix: str, first: int, count: int = 6) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:6", f"#EXT-X-MEDIA-SEQUENCE:{first}"]
    for n in range(first, first + count):
        lines.extend(["#EXTINF:6.000000,", f"{prefix}_{n}.ts"])
    return "\n".join(lines) + "\n"


def _make_segments(root: Path, prefix: str, first: int, count: int = 6):
    for n in range(first, first + count):
        (root / f"{prefix}_{n}.ts").write_bytes((f"{prefix}-{n}" * 100).encode())


def _media_sequence(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            return int(line.split(":", 1)[1])
    raise AssertionError("missing media sequence")


def _uris(text: str):
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_parse_media_playlist():
    target, segments = parse_media_playlist(_playlist("guide", 100, 2))
    assert target == 6
    assert segments == [(6.0, "guide_100.ts"), (6.0, "guide_101.ts")]


def test_mix_owns_continuous_sequence_and_inserts_discontinuity(tmp_path):
    state = ChannelMixHLSState(tmp_path, window_size=10)
    _make_segments(tmp_path, "guide", 100)
    first = state.build("rsmc-guide", _playlist("guide", 100))
    assert first is not None
    first_uris = _uris(first)
    assert len(first_uris) == 6
    assert "#EXT-X-DISCONTINUITY" not in first.splitlines()
    assert all(uri.startswith("channel_mix_") for uri in first_uris)

    # New guide segment advances the mix timeline normally.
    _make_segments(tmp_path, "guide", 106, 1)
    second = state.build("rsmc-guide", _playlist("guide", 101, 6))
    assert second is not None
    assert _media_sequence(second) >= _media_sequence(first)

    # Weather uses the SAME source sequence numbers.  The mix must still create
    # new sequence numbers and explicitly signal the MPEG-TS timestamp change.
    _make_segments(tmp_path, "weather", 101, 6)
    switched = state.build("rsmc-weather", _playlist("weather", 101, 6))
    assert switched is not None
    assert "#EXT-X-DISCONTINUITY" in switched.splitlines()
    switched_uris = _uris(switched)
    assert switched_uris[-1] != _uris(second)[-1]


def test_last_to_first_wrap_is_a_real_hls_transition(tmp_path):
    state = ChannelMixHLSState(tmp_path, window_size=10)
    sources = ["guide", "weather", "news", "traffic", "guide"]
    seq = 200
    outputs = []
    for source in sources:
        _make_segments(tmp_path, source, seq, 6)
        text = state.build(f"rsmc-{source}", _playlist(source, seq, 6))
        assert text
        outputs.append(text)
        seq += 1

    # Every actual source transition, including traffic -> guide wrap, carries
    # a discontinuity and keeps the same mix-owned URI namespace.
    assert "#EXT-X-DISCONTINUITY" in outputs[-1].splitlines()
    assert all(uri.startswith("channel_mix_") for uri in _uris(outputs[-1]))
    # Playlist remains live; a client is never told the stream ended.
    assert "#EXT-X-ENDLIST" not in outputs[-1]


def test_hardlinked_mix_segment_survives_source_deletion(tmp_path):
    state = ChannelMixHLSState(tmp_path, window_size=10)
    _make_segments(tmp_path, "traffic", 300)
    text = state.build("rsmc-traffic", _playlist("traffic", 300, 6))
    uri = _uris(text)[-1]
    (tmp_path / "traffic_305.ts").unlink()
    assert (tmp_path / uri).is_file()


def test_switch_does_not_backfill_old_new_source_history(tmp_path):
    state = ChannelMixHLSState(tmp_path, window_size=10)
    _make_segments(tmp_path, "guide", 400, 6)
    state.build("rsmc-guide", _playlist("guide", 400, 6))

    _make_segments(tmp_path, "weather", 400, 6)
    switched = state.build("rsmc-weather", _playlist("weather", 400, 6))
    switch_uris = _uris(switched)

    # On the next reload only a genuinely newer segment should be appended;
    # weather_400..403 must never be backfilled after weather_404..405.
    _make_segments(tmp_path, "weather", 406, 1)
    follow = state.build("rsmc-weather", _playlist("weather", 401, 6))
    follow_uris = _uris(follow)
    assert len(follow_uris) == min(10, len(switch_uris) + 1)
    assert follow_uris[-1] not in switch_uris
