import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def load_functions(*names):
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    ns = {"subprocess": __import__("subprocess")}
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(APP), "exec"), ns)
    return ns


class PlexHDHRCompatibilityTests(unittest.TestCase):
    def test_local_rsmc_hls_is_copy_remuxed_and_realtime_paced(self):
        fn = load_functions("_build_hdhomerun_ffmpeg_command")["_build_hdhomerun_ffmpeg_command"]
        cmd = fn("/tmp/news.m3u8", local_hls=True)
        joined = " ".join(cmd)
        self.assertIn("-re", cmd)
        self.assertIn("-live_start_index", cmd)
        self.assertIn("-c copy", joined)
        self.assertNotIn("anullsrc", joined)

    def test_external_source_remains_unpaced_copy_remux(self):
        fn = load_functions("_build_hdhomerun_ffmpeg_command")["_build_hdhomerun_ffmpeg_command"]
        cmd = fn("http://rsmc/hls/weather.m3u8")
        joined = " ".join(cmd)
        self.assertNotIn("-re", cmd)
        self.assertNotIn("-live_start_index", cmd)
        self.assertIn("-c copy", joined)

    def test_hdhr_guide_uses_media_playlist(self):
        text = APP.read_text(encoding="utf-8")
        self.assertIn('stream_url = base_url + "/hls/guide.m3u8"', text)


if __name__ == "__main__":
    unittest.main()
