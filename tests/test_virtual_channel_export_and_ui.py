from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_main_export_checkbox_is_independent_from_channel_enablement():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    block = text[text.index("def coerce_form"):text.index("def _coerce_int", text.index("def coerce_form"))]
    assert '"virtual_channels_export_enabled"' in block
    assert '"weather_channel_enabled": any(' not in block
    assert '"traffic_channel_enabled": any(' not in block
    assert '"news_channel_enabled": any(' not in block
    assert '"channel_mix_enabled": any(' not in block


def test_export_filter_keeps_guide_when_optional_export_disabled():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _build_exported_virtual_channel_entries" in text
    assert 'entry.get("id") == VIRTUAL_GUIDE_CHANNEL_ID' in text
    assert "_build_channels_m3u_content(_build_exported_virtual_channel_entries" in text
    assert "_build_channels_xmltv_content(_build_exported_virtual_channel_entries" in text


def test_virtual_channel_cards_are_consistent_collapsed_accordions():
    text = (ROOT / "app/templates/virtual_channels.html").read_text(encoding="utf-8")
    for body_id in ("vc-weather-body", "vc-traffic-body", "vc-news-body", "vc-mix-body"):
        assert f'aria-controls="{body_id}"' in text
        assert f'id="{body_id}" hidden' in text
    assert text.count('class="vc-card-toggle"') == 4
    assert text.index('id="channel-mix-card"') < text.index('</div><!-- /vc-page -->')


def test_channel_badges_follow_channel_names_consistently():
    text = (ROOT / "app/templates/virtual_channels.html").read_text(encoding="utf-8")
    pairs = [
        ("Weather Channel", "CH 2"),
        ("Simulated Traffic Channel", "CH 3"),
        ("News Now", "CH 4"),
        ("Channel Mix", "CH 5"),
    ]
    for name, badge in pairs:
        pos = text.index(f"<h2>{name}</h2>")
        badge_pos = text.index(badge, pos)
        assert badge_pos > pos
        assert badge_pos - pos < 180


def test_collapsed_arrow_is_up_and_expanded_css_flips_it_down():
    text = (ROOT / "app/templates/virtual_channels.html").read_text(encoding="utf-8")
    assert text.count('class="vc-card-toggle" aria-hidden="true">&#9650;</span>') == 4
    assert '.vc-card-head[aria-expanded="true"] .vc-card-toggle { transform: rotate(180deg); }' in text


def test_channel_mix_guide_source_is_always_media_playlist_not_master_playlist():
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    start = text.index('def _channel_mix_registry')
    end = text.index('def _get_channel_mix_config', start)
    block = text[start:end]
    assert '"playlist": "guide.m3u8"' in block
    assert '"playlist": "master.m3u8"' not in block
