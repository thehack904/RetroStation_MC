import json

import app.guide_state as guide_state


def test_legacy_channel_group_does_not_filter_source_channels(tmp_path):
    state = guide_state.build_state(
        {
            "theme": "retrostation_mc",
            "title": "Guide Channel",
            "guide_minutes": 90,
            "visible_rows": 8,
            "channel_group": "TV Guide",
        },
        [
            {"id": "news", "name": "News", "group": "News"},
            {"id": "movies", "name": "Movies", "group": "Movies"},
        ],
        {},
        output_path=tmp_path / "guide_state.json",
    )

    channels = [channel for page in state["pages"] for channel in page]
    assert [channel["id"] for channel in channels] == ["news", "movies"]
    assert channels
    assert json.loads((tmp_path / "guide_state.json").read_text(encoding="utf-8"))["pages"] == state["pages"]
