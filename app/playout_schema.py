from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Allowed item types
# ---------------------------------------------------------------------------

ITEM_TYPES = {"video", "virtual_channel", "promo", "standby", "preview_channel"}

# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


class PlayoutValidationError(ValueError):
    """Raised when a playout document fails schema validation."""


# ---------------------------------------------------------------------------
# Schema definition (plain dicts — no external dependencies)
# ---------------------------------------------------------------------------

#: Minimum required fields for every item entry.
_ITEM_REQUIRED_FIELDS: Dict[str, type] = {
    "type": str,
    "source": str,
    "duration": (int, float),  # type: ignore[assignment]
}


def _type_label(expected: Any) -> str:
    """Return a human-readable label for a type or tuple of types."""
    if isinstance(expected, type):
        return expected.__name__
    return " or ".join(t.__name__ for t in expected)


def _validate_item(item: Any, index: int) -> None:
    """Validate a single playout item dict."""
    if not isinstance(item, dict):
        raise PlayoutValidationError(
            f"items[{index}]: expected an object, got {type(item).__name__!r}"
        )

    # Required fields
    for field, expected in _ITEM_REQUIRED_FIELDS.items():
        if field not in item:
            raise PlayoutValidationError(
                f"items[{index}]: missing required field {field!r}"
            )
        if not isinstance(item[field], expected):
            raise PlayoutValidationError(
                f"items[{index}].{field}: expected {_type_label(expected)}, "
                f"got {type(item[field]).__name__!r}"
            )

    # Type allowlist
    item_type = item["type"]
    if item_type not in ITEM_TYPES:
        allowed = ", ".join(sorted(ITEM_TYPES))
        raise PlayoutValidationError(
            f"items[{index}].type: {item_type!r} is not a recognised type. "
            f"Allowed values: {allowed}"
        )

    # Duration must be positive
    if item["duration"] <= 0:
        raise PlayoutValidationError(
            f"items[{index}].duration: must be a positive number, got {item['duration']!r}"
        )


def validate_playout_document(doc: Any) -> Dict[str, Any]:
    """Validate *doc* against the playout document schema.

    Returns the validated document dict on success.
    Raises :class:`PlayoutValidationError` with a descriptive message on
    failure.
    """
    if not isinstance(doc, dict):
        raise PlayoutValidationError(
            f"Playout document must be a JSON object, got {type(doc).__name__!r}"
        )

    # Required top-level field: channel
    if "channel" not in doc:
        raise PlayoutValidationError("Missing required top-level field 'channel'")
    if not isinstance(doc["channel"], str) or not doc["channel"].strip():
        raise PlayoutValidationError(
            f"'channel' must be a non-empty string, got {doc['channel']!r}"
        )

    # Required top-level field: items
    if "items" not in doc:
        raise PlayoutValidationError("Missing required top-level field 'items'")
    if not isinstance(doc["items"], list):
        raise PlayoutValidationError(
            f"'items' must be an array, got {type(doc['items']).__name__!r}"
        )
    if len(doc["items"]) == 0:
        raise PlayoutValidationError("'items' must contain at least one entry")

    for index, item in enumerate(doc["items"]):
        _validate_item(item, index)

    return doc


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_playout_document(source: str | Path) -> Dict[str, Any]:
    """Load and validate a playout document from *source*.

    *source* may be a filesystem path or a raw JSON string.

    Returns the validated document dict.
    Raises :class:`PlayoutValidationError` for schema violations and
    :class:`json.JSONDecodeError` for malformed JSON.
    """
    path = Path(source)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = str(source)

    doc = json.loads(text)
    return validate_playout_document(doc)


def parse_playout_items(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the validated list of playout items from *doc*."""
    return copy.deepcopy(doc["items"])
