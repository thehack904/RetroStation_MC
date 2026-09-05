import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"

def _load(name):
    tree = ast.parse(APP.read_text())
    ns = {}
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(APP), 'exec'), ns)
    return ns[name]

def test_local_hls_is_realtime_and_live_edge():
    fn = _load('_build_hdhomerun_ffmpeg_command')
    cmd = fn('/tmp/guide.m3u8', local_hls=True)
    i = cmd.index('-i')
    assert '-re' in cmd[:i]
    assert cmd[cmd.index('-live_start_index') + 1] == '-1'

def test_external_source_preserves_existing_behavior():
    fn = _load('_build_hdhomerun_ffmpeg_command')
    cmd = fn('http://example/live.m3u8')
    assert '-re' not in cmd
    assert '-live_start_index' not in cmd
