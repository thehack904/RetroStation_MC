import json
from pathlib import Path

from app.guide_preview import calculate_preview_layout
from app.guide_state import load_theme
from app.renderer import GuideRenderer


def _renderer(tmp_path: Path, resolution: str = "1280x720", aspect_ratio: str = "16:9") -> GuideRenderer:
    theme = load_theme("retrostation_mc")
    layout = calculate_preview_layout(resolution, aspect_ratio, theme.get("layout", {}), "16:9")
    state = {
        "theme": "retrostation_mc",
        "title": "Guide Channel",
        "theme_data": theme,
        "display": {
            "resolution": resolution,
            "fps": 15,
            "page_seconds": 12,
            "visible_rows": 4,
            "guide_minutes": 90,
            "transition": "cut",
            "timezone": "utc",
            "browser_timezone": "",
            "preview_enabled": True,
            "preview_layout": layout,
            "guide_message_enabled": True,
            "guide_message_text": (
                "[message]\nTONIGHT ON RETROSTATION\n\nClassic Horror Night\n[/message]\n\n"
                "[blank:15]\n\n"
                "[message:45]\nSATURDAY MORNING CARTOONS\nStarts tomorrow at 6:00 AM\n[/message]"
            ),
            "guide_message_interval_seconds": 8,
        },
        "time_window": {
            "start": "2026-08-13T14:00:00+00:00",
            "end": "2026-08-13T15:30:00+00:00",
        },
        "pages": [[]],
    }
    state_path = tmp_path / f"guide-state-{resolution}.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return GuideRenderer(state_path)


def test_blank_lines_are_preserved_not_separators(tmp_path):
    renderer = _renderer(tmp_path)
    slides = renderer._split_guide_messages("ONE\n\nLine two\n\n\nTHREE")
    assert slides == [["ONE", "", "Line two", "", "", "THREE"]]


def test_message_blocks_define_rotation_and_preserve_spacing(tmp_path):
    renderer = _renderer(tmp_path)
    slides = renderer._parse_guide_message_slides(
        "[message]\nMESSAGE ONE\n\nLine three\n[/message]\n\n"
        "[message:45]\nMESSAGE TWO\n\nSecond section\n[/message]"
    )
    assert slides == [
        {"lines": ["MESSAGE ONE", "", "Line three"], "blank": False, "duration": None},
        {"lines": ["MESSAGE TWO", "", "Second section"], "blank": False, "duration": 45},
    ]


def test_message_and_blank_directives_are_timed(tmp_path):
    renderer = _renderer(tmp_path)
    slides = renderer._parse_guide_message_slides(
        "[message]\nMESSAGE ONE\n[/message]\n"
        "[blank]\n"
        "[blank:90]\n"
        "[message:45]\nMESSAGE TWO\n[/message]"
    )
    assert slides == [
        {"lines": ["MESSAGE ONE"], "blank": False, "duration": None},
        {"lines": [], "blank": True, "duration": None},
        {"lines": [], "blank": True, "duration": 90},
        {"lines": ["MESSAGE TWO"], "blank": False, "duration": 45},
    ]

    # With a 30-second default interval, the cycle is:
    # message 30s -> blank 30s -> blank 90s -> message 45s.
    assert renderer._select_guide_message_slide(slides, 0, 30) == slides[0]
    assert renderer._select_guide_message_slide(slides, 29.9, 30) == slides[0]
    assert renderer._select_guide_message_slide(slides, 30, 30) == slides[1]
    assert renderer._select_guide_message_slide(slides, 59.9, 30) == slides[1]
    assert renderer._select_guide_message_slide(slides, 60, 30) == slides[2]
    assert renderer._select_guide_message_slide(slides, 149.9, 30) == slides[2]
    assert renderer._select_guide_message_slide(slides, 150, 30) == slides[3]
    assert renderer._select_guide_message_slide(slides, 194.9, 30) == slides[3]
    assert renderer._select_guide_message_slide(slides, 195, 30) == slides[0]


def test_plain_text_is_one_message_even_with_blank_lines(tmp_path):
    renderer = _renderer(tmp_path)
    slides = renderer._parse_guide_message_slides("TITLE\n\nLine after a blank\n\nAnother line")
    assert slides == [
        {
            "lines": ["TITLE", "", "Line after a blank", "", "Another line"],
            "blank": False,
            "duration": None,
        }
    ]


def test_durations_are_capped_at_one_hour(tmp_path):
    renderer = _renderer(tmp_path)
    slides = renderer._parse_guide_message_slides(
        "[message:99999]\nLONG MESSAGE\n[/message]\n[blank:99999]"
    )
    assert slides[0] == {"lines": ["LONG MESSAGE"], "blank": False, "duration": 3600}
    assert slides[1] == {"lines": [], "blank": True, "duration": 3600}


def test_wrap_preserves_blank_display_lines(tmp_path):
    renderer = _renderer(tmp_path)
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1280, 720), "black")
    draw = ImageDraw.Draw(image)
    wrapped = renderer._wrap_guide_message_lines(
        draw, ["TITLE", "", "Line after blank"], max_width=500, max_lines=4
    )
    assert wrapped == ["TITLE", "", "Line after blank"]


def test_guide_message_renders_at_all_supported_resolutions(tmp_path):
    for resolution, aspect_ratio in (
        ("1280x720", "16:9"),
        ("1920x1080", "16:9"),
        ("960x720", "4:3"),
        ("1440x1080", "4:3"),
    ):
        renderer = _renderer(tmp_path, resolution, aspect_ratio)
        frame = renderer.draw_frame(epoch_time=1786631370.0)
        assert frame.size == tuple(int(value) for value in resolution.split("x"))


def test_message_is_not_drawn_without_preview(tmp_path):
    renderer = _renderer(tmp_path)
    renderer.reload_if_needed()
    renderer.state["display"]["preview_enabled"] = False
    frame = renderer.draw_frame(epoch_time=1786631370.0)
    assert frame.size == (1280, 720)
