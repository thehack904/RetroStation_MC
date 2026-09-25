import ast
from pathlib import Path


def _manager_source():
    return Path("app/manager.py").read_text(encoding="utf-8")


def test_local_files_use_muxed_preview_relay_not_jpeg_udp_split():
    source = _manager_source()
    tree = ast.parse(source)
    start = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "start_pipeline")
    body = ast.get_source_segment(source, start)
    assert "mpegts_overlay_source = self._start_mpegts_preview_relay_locked(config)" in body
    assert 'raw_preview_source.startswith(("http://", "https://"))' not in body


def test_muxed_relay_accepts_local_sources_and_realtime_input_args():
    source = _manager_source()
    tree = ast.parse(source)
    relay = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_start_mpegts_preview_relay_locked")
    body = ast.get_source_segment(source, relay)
    assert 'not source.startswith(("http://", "https://"))' not in body
    assert "_preview_source_input_args(source, transport)" in body
    assert '"-map", "0:v:0", "-map", "0:a:0?"' in body
    assert '"-f", "hls"' in body
