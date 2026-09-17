import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app.py'

class HdhrLowLatencyRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)

    def _function_source(self, name):
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return ast.get_source_segment(self.source, node)
        self.fail(f'missing {name}')

    def test_tuner_open_does_not_run_ffprobe(self):
        body = self._function_source('_hdhomerun_mpegts_chunks')
        self.assertNotIn('ffprobe', body)
        self.assertNotIn('_hdhomerun_stream_has_audio', body)

    def test_remux_matches_proven_copy_path(self):
        body = self._function_source('_build_hdhomerun_ffmpeg_command')
        for token in ['+genpts', '0:v:0?', '0:a:0?', '"copy"', '"mpegts"', '"pipe:1"']:
            self.assertIn(token, body)
        self.assertNotIn('anullsrc', body)
        self.assertNotIn('max_interleave_delta', body)
        self.assertNotIn('avoid_negative_ts', body)

    def test_all_generated_channels_use_local_hls_path(self):
        helper = self._function_source('_hdhomerun_local_playlist_for_channel')
        for name in [
            'VIRTUAL_GUIDE_CHANNEL_ID', 'VIRTUAL_WEATHER_CHANNEL_ID',
            'VIRTUAL_TRAFFIC_CHANNEL_ID', 'VIRTUAL_NEWS_CHANNEL_ID',
            'VIRTUAL_CHANNEL_MIX_ID'
        ]:
            self.assertIn(name, helper)
        body = self._function_source('hdhomerun_channel_tune')
        self.assertIn('_hdhomerun_local_playlist_for_channel(channel)', body)
        self.assertIn('tuner_input = str(local_playlist)', body)

if __name__ == '__main__':
    unittest.main()
