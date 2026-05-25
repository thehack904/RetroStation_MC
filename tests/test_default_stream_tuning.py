from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config_store import DEFAULT_CONFIG
from app import guide_state


class DefaultStreamTuningTests(unittest.TestCase):
    def test_default_config_uses_stability_profile(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["theme"], "retrostation_mc")
        self.assertEqual(DEFAULT_CONFIG["resolution"], "1280x720")
        self.assertEqual(DEFAULT_CONFIG["fps"], 15)
        self.assertEqual(DEFAULT_CONFIG["segment_seconds"], 6)
        self.assertEqual(DEFAULT_CONFIG["diag_delay_segments"], 2)
        self.assertEqual(DEFAULT_CONFIG["diag_min_buffer_secs"], 18)
        self.assertEqual(DEFAULT_CONFIG["diag_min_buffer_segments"], 3)

    def test_build_state_uses_15fps_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_state = Path(temp_dir) / "guide_state.json"
            with patch.object(guide_state, "STATE_PATH", temp_state):
                with patch.object(guide_state, "load_theme", return_value={"colors": {}, "layout": {}}) as load_theme:
                    state = guide_state.build_state(config={}, channels=[], programmes={})
        load_theme.assert_called_once_with("retrostation_mc")
        self.assertEqual(state["theme"], "retrostation_mc")
        self.assertEqual(state["display"]["resolution"], "1280x720")
        self.assertEqual(state["display"]["fps"], 15)


if __name__ == "__main__":
    unittest.main()
