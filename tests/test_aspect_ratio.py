from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from werkzeug.datastructures import MultiDict

from app.config_store import DEFAULT_CONFIG


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_guide_web_app_aspect_ratio", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_app = _load_app_module()


class NormalizeAspectRatioTests(unittest.TestCase):
    def test_default_config_has_16_9_aspect_ratio(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["aspect_ratio"], "16:9")

    def test_normalize_accepts_16_9(self) -> None:
        self.assertEqual(_app._normalize_aspect_ratio("16:9"), "16:9")

    def test_normalize_accepts_4_3(self) -> None:
        self.assertEqual(_app._normalize_aspect_ratio("4:3"), "4:3")

    def test_normalize_rejects_invalid_returns_default(self) -> None:
        self.assertEqual(_app._normalize_aspect_ratio("21:9"), DEFAULT_CONFIG["aspect_ratio"])

    def test_normalize_none_returns_default(self) -> None:
        self.assertEqual(_app._normalize_aspect_ratio(None), DEFAULT_CONFIG["aspect_ratio"])

    def test_normalize_empty_string_returns_default(self) -> None:
        self.assertEqual(_app._normalize_aspect_ratio(""), DEFAULT_CONFIG["aspect_ratio"])


class CoerceFormAspectRatioTests(unittest.TestCase):
    def test_coerce_form_defaults_aspect_ratio_when_missing(self) -> None:
        cfg = _app.coerce_form({})
        self.assertEqual(cfg["aspect_ratio"], "16:9")

    def test_coerce_form_accepts_16_9(self) -> None:
        cfg = _app.coerce_form(MultiDict([("aspect_ratio", "16:9")]))
        self.assertEqual(cfg["aspect_ratio"], "16:9")

    def test_coerce_form_accepts_4_3(self) -> None:
        cfg = _app.coerce_form(MultiDict([("aspect_ratio", "4:3")]))
        self.assertEqual(cfg["aspect_ratio"], "4:3")

    def test_coerce_form_rejects_invalid_aspect_ratio(self) -> None:
        cfg = _app.coerce_form(MultiDict([("aspect_ratio", "invalid")]))
        self.assertEqual(cfg["aspect_ratio"], "16:9")

    def test_coerce_form_preserves_resolution_with_aspect_ratio(self) -> None:
        cfg = _app.coerce_form(
            MultiDict([("aspect_ratio", "4:3"), ("resolution", "960x720")])
        )
        self.assertEqual(cfg["aspect_ratio"], "4:3")
        self.assertEqual(cfg["resolution"], "960x720")


if __name__ == "__main__":
    unittest.main()
