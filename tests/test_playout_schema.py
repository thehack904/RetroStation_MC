from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.playout_schema import (
    ITEM_TYPES,
    PlayoutValidationError,
    parse_playout_document,
    parse_playout_items,
    validate_playout_document,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_DOC = {
    "channel": "preview-channel",
    "items": [
        {"type": "video", "source": "/media/intro.mp4", "duration": 90},
        {"type": "virtual_channel", "source": "weather", "duration": 300},
    ],
}


def _doc(**overrides):
    """Return a copy of the valid document with top-level overrides applied."""
    base = json.loads(json.dumps(_VALID_DOC))
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# validate_playout_document
# ---------------------------------------------------------------------------


class ValidatePlayoutDocumentTests(unittest.TestCase):
    # --- happy path ---------------------------------------------------------

    def test_valid_document_is_returned(self) -> None:
        result = validate_playout_document(_VALID_DOC)
        self.assertEqual(result["channel"], "preview-channel")
        self.assertEqual(len(result["items"]), 2)

    def test_all_item_types_are_accepted(self) -> None:
        for item_type in ITEM_TYPES:
            doc = {
                "channel": "test",
                "items": [{"type": item_type, "source": "src", "duration": 10}],
            }
            result = validate_playout_document(doc)
            self.assertEqual(result["items"][0]["type"], item_type)

    def test_float_duration_is_accepted(self) -> None:
        doc = _doc(
            items=[{"type": "video", "source": "/media/clip.mp4", "duration": 12.5}]
        )
        result = validate_playout_document(doc)
        self.assertEqual(result["items"][0]["duration"], 12.5)

    # --- top-level errors ---------------------------------------------------

    def test_non_dict_raises(self) -> None:
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document([])
        self.assertIn("JSON object", str(ctx.exception))

    def test_missing_channel_raises(self) -> None:
        doc = {"items": _VALID_DOC["items"]}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(doc)
        self.assertIn("channel", str(ctx.exception))

    def test_empty_channel_raises(self) -> None:
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(channel="   "))
        self.assertIn("channel", str(ctx.exception))

    def test_non_string_channel_raises(self) -> None:
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(channel=42))
        self.assertIn("channel", str(ctx.exception))

    def test_missing_items_raises(self) -> None:
        doc = {"channel": "ch1"}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(doc)
        self.assertIn("items", str(ctx.exception))

    def test_items_not_list_raises(self) -> None:
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items={}))
        self.assertIn("items", str(ctx.exception))

    def test_empty_items_raises(self) -> None:
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[]))
        self.assertIn("items", str(ctx.exception))

    # --- item-level errors --------------------------------------------------

    def test_item_not_dict_raises(self) -> None:
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=["not-a-dict"]))
        self.assertIn("items[0]", str(ctx.exception))

    def test_item_missing_type_raises(self) -> None:
        item = {"source": "/media/x.mp4", "duration": 10}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("type", str(ctx.exception))

    def test_item_missing_source_raises(self) -> None:
        item = {"type": "video", "duration": 10}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("source", str(ctx.exception))

    def test_item_missing_duration_raises(self) -> None:
        item = {"type": "video", "source": "/media/x.mp4"}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("duration", str(ctx.exception))

    def test_unknown_item_type_raises(self) -> None:
        item = {"type": "unsupported_type", "source": "src", "duration": 10}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("unsupported_type", str(ctx.exception))

    def test_zero_duration_raises(self) -> None:
        item = {"type": "video", "source": "src", "duration": 0}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("duration", str(ctx.exception))

    def test_negative_duration_raises(self) -> None:
        item = {"type": "video", "source": "src", "duration": -5}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("duration", str(ctx.exception))

    def test_non_numeric_duration_raises(self) -> None:
        item = {"type": "video", "source": "src", "duration": "30s"}
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=[item]))
        self.assertIn("duration", str(ctx.exception))

    def test_error_message_includes_item_index(self) -> None:
        items = [
            {"type": "video", "source": "ok.mp4", "duration": 10},
            {"type": "video", "source": "bad.mp4", "duration": -1},
        ]
        with self.assertRaises(PlayoutValidationError) as ctx:
            validate_playout_document(_doc(items=items))
        self.assertIn("items[1]", str(ctx.exception))


# ---------------------------------------------------------------------------
# parse_playout_document
# ---------------------------------------------------------------------------


class ParsePlayoutDocumentTests(unittest.TestCase):
    def test_parse_from_file_path(self) -> None:
        doc_text = json.dumps(_VALID_DOC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "playout.json"
            path.write_text(doc_text, encoding="utf-8")
            result = parse_playout_document(path)
        self.assertEqual(result["channel"], "preview-channel")

    def test_parse_from_path_string(self) -> None:
        doc_text = json.dumps(_VALID_DOC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "playout.json"
            path.write_text(doc_text, encoding="utf-8")
            result = parse_playout_document(str(path))
        self.assertEqual(result["channel"], "preview-channel")

    def test_parse_from_raw_json_string(self) -> None:
        result = parse_playout_document(json.dumps(_VALID_DOC))
        self.assertEqual(result["channel"], "preview-channel")

    def test_invalid_json_raises_json_decode_error(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            parse_playout_document("{not valid json")

    def test_invalid_schema_raises_playout_validation_error(self) -> None:
        bad_doc = json.dumps({"channel": "ch", "items": []})
        with self.assertRaises(PlayoutValidationError):
            parse_playout_document(bad_doc)

    def test_sample_file_is_valid(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        sample = repo_root / "sample_data" / "playout_example.json"
        result = parse_playout_document(sample)
        self.assertEqual(result["channel"], "preview-channel")
        self.assertGreater(len(result["items"]), 0)


# ---------------------------------------------------------------------------
# parse_playout_items
# ---------------------------------------------------------------------------


class ParsePlayoutItemsTests(unittest.TestCase):
    def test_returns_list_of_items(self) -> None:
        items = parse_playout_items(_VALID_DOC)
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 2)

    def test_items_are_independent_copies(self) -> None:
        doc = json.loads(json.dumps(_VALID_DOC))
        items = parse_playout_items(doc)
        items[0]["type"] = "mutated"
        # Original document should be unaffected
        self.assertEqual(doc["items"][0]["type"], "video")


if __name__ == "__main__":
    unittest.main()
