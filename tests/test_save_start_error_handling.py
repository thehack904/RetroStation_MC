from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_guide_web_app", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SaveStartErrorHandlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()

    def setUp(self) -> None:
        self.mock_store = Mock()
        self.mock_store.get_config.return_value = {}
        self.mock_store.save_config.return_value = None
        self.mock_store.get_recent_events.return_value = [{"created_at": "2026-01-01T00:00:00+00:00"}]
        self.mock_store.count_events.return_value = 1

        self.mock_manager = Mock()
        self.mock_manager.status.return_value = {
            "pipeline_active": False,
            "renderer_running": False,
            "ffmpeg_running": False,
            "guide_buffered": False,
            "last_refresh_status": "ok",
        }
        self.mock_manager.logger = Mock()

        self.web.store = self.mock_store
        self.web.manager = self.mock_manager
        self.client = self.web.app.test_client()

    def test_save_and_start_reports_start_failure_not_save_failure(self) -> None:
        self.mock_manager.start_pipeline.side_effect = Exception()

        response = self.client.post(
            "/config",
            data={"action": "start_restart"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        flash_messages = [message for _category, message in flashes]
        self.assertIn("Configuration saved, but guide failed to start: Exception", flash_messages)
        self.assertNotIn("Failed to save configuration: Exception", flash_messages)
        self.mock_store.save_config.assert_called_once()
        error_messages = [call.args[1] for call in self.mock_manager.logger.error.call_args_list]
        self.assertTrue(any("route=/config" in message and "phase=start_restart" in message for message in error_messages))
        self.assertTrue(any("http.traceback" == call.args[0] for call in self.mock_manager.logger.error.call_args_list))

    def test_restart_route_uses_non_empty_exception_fallback(self) -> None:
        self.mock_manager.restart_pipeline.side_effect = Exception()

        response = self.client.post("/restart", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as sess:
            flashes = sess.get("_flashes", [])
        flash_messages = [message for _category, message in flashes]
        self.assertIn("Pipeline restart failed: Exception", flash_messages)
        error_messages = [call.args[1] for call in self.mock_manager.logger.error.call_args_list]
        self.assertTrue(any("route=/restart" in message and "phase=restart_pipeline" in message for message in error_messages))


if __name__ == "__main__":
    unittest.main()
