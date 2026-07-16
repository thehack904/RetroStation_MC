# Playout Document Schema

A **playout document** is a JSON file (or JSON string) that describes a
scheduled sequence of content items for a RetroStation MC channel. The
scheduler service reads playout documents to determine what to play and when.

---

## Top-level structure

| Field     | Type            | Required | Description                                         |
|-----------|-----------------|----------|-----------------------------------------------------|
| `channel` | string          | ✅       | Identifier of the target channel (e.g. `"preview-channel"`). |
| `items`   | array of objects| ✅       | Ordered list of content items to play. Must contain at least one entry. |

---

## Item structure

Each object in `items` must contain the following fields:

| Field      | Type             | Required | Description                                              |
|------------|------------------|----------|----------------------------------------------------------|
| `type`     | string           | ✅       | Content type. See [Item types](#item-types) below.       |
| `source`   | string           | ✅       | Path, URL, or named source for the content.              |
| `duration` | number (seconds) | ✅       | How long (in seconds) to show this item. Must be positive. |

Additional fields may be present and will be preserved for future renderer and
scheduler use, but are not validated by the current schema.

---

## Item types

| Type              | Description                                                   |
|-------------------|---------------------------------------------------------------|
| `video`           | A pre-recorded video file.                                    |
| `promo`           | A short promotional clip.                                     |
| `virtual_channel` | A virtual channel output (e.g. `"weather"`, `"news"`).        |
| `standby`         | Standby / holding content.                                    |
| `preview_channel` | A Preview Channel block (e.g. the guide overlay channel).     |

---

## Example

```json
{
  "channel": "preview-channel",
  "items": [
    {
      "type": "video",
      "source": "/media/promos/intro.mp4",
      "duration": 90
    },
    {
      "type": "promo",
      "source": "/media/promos/network_id.mp4",
      "duration": 30
    },
    {
      "type": "virtual_channel",
      "source": "weather",
      "duration": 300
    },
    {
      "type": "standby",
      "source": "default",
      "duration": 60
    },
    {
      "type": "preview_channel",
      "source": "guide",
      "duration": 120
    }
  ]
}
```

A working copy of this example is stored at
[`sample_data/playout_example.json`](../sample_data/playout_example.json).

---

## Parsing and validation

Use the helpers in `app/playout_schema.py`:

```python
from app.playout_schema import parse_playout_document, PlayoutValidationError

# Load from a file path or a raw JSON string
try:
    doc = parse_playout_document("/path/to/playout.json")
except PlayoutValidationError as exc:
    print(f"Invalid playout document: {exc}")
```

`parse_playout_document` returns the validated document dict on success.

It raises:
- `app.playout_schema.PlayoutValidationError` — schema violation with a
  descriptive message identifying the field and reason.
- `json.JSONDecodeError` — the input is not valid JSON.

To iterate over items after parsing:

```python
from app.playout_schema import parse_playout_items

items = parse_playout_items(doc)
for item in items:
    print(item["type"], item["source"], item["duration"])
```

`parse_playout_items` returns a deep copy of the item list so callers can
modify items without affecting the original document.

---

## Validation rules

| Rule                                          | Error produced                                              |
|-----------------------------------------------|-------------------------------------------------------------|
| Document is not a JSON object                 | `Playout document must be a JSON object`                    |
| `channel` is missing                          | `Missing required top-level field 'channel'`                |
| `channel` is empty or not a string            | `'channel' must be a non-empty string`                      |
| `items` is missing                            | `Missing required top-level field 'items'`                  |
| `items` is not an array                       | `'items' must be an array`                                  |
| `items` is empty                              | `'items' must contain at least one entry`                   |
| An item is not a JSON object                  | `items[N]: expected an object`                              |
| An item is missing `type`, `source`, or `duration` | `items[N]: missing required field '<field>'`           |
| `type` is not a recognised value              | `items[N].type: '<value>' is not a recognised type`         |
| `duration` is zero or negative                | `items[N].duration: must be a positive number`              |
| `duration` is not a number                    | `items[N].duration: expected int or float`                  |

---

## Design notes

- The schema is intentionally minimal. Extra fields on items are ignored and
  preserved, allowing future versions to add renderer hints, transition
  directives, or metadata without breaking existing documents.
- `duration` accepts both integers and floats to accommodate sub-second
  precision where needed.
- The `channel` field is free-form to support arbitrary channel identifiers
  defined elsewhere in the system.
