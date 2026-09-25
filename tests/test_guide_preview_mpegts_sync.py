import ast
from pathlib import Path


def _function_source(name: str) -> str:
    source = Path("app/manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(source, node)


def test_mpegts_preview_relay_rebases_audio_and_video_timestamps():
    source = _function_source("_start_mpegts_preview_relay_locked")
    assert '"-fflags", "+genpts+discardcorrupt"' in source
    assert '"setpts=PTS-STARTPTS"' in source
    assert 'audio_filter = "aresample=48000:async=1:first_pts=0"' in source
    assert '"-avoid_negative_ts", "make_zero"' in source
    assert '"-muxdelay", "0", "-muxpreload", "0"' in source


def test_preview_relay_uses_common_muxed_timeline_for_local_and_network_sources():
    source = _function_source("_start_mpegts_preview_relay_locked")
    assert '_preview_source_input_args(source, transport)' in source
    assert '"-map", "0:v:0", "-map", "0:a:0?"' in source
    assert '"-f", "hls"' in source
    assert 'not source.startswith(("http://", "https://"))' not in source
