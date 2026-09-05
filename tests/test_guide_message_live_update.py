import json
from pathlib import Path

import app.guide_state as guide_state


def test_patch_display_state_updates_only_display(tmp_path, monkeypatch):
    state_path = tmp_path / 'guide_state.json'
    state_path.write_text(json.dumps({
        'display': {'preview_enabled': True, 'guide_message_enabled': False},
        'pages': [{'keep': 'me'}],
        'theme': 'retrostation_mc',
    }), encoding='utf-8')
    monkeypatch.setattr(guide_state, 'STATE_PATH', state_path)

    assert guide_state.patch_display_state({
        'guide_message_enabled': True,
        'guide_message_text': 'TONIGHT\nClassic Horror Night',
        'guide_message_interval_seconds': 8,
    }) is True

    updated = json.loads(state_path.read_text(encoding='utf-8'))
    assert updated['display']['preview_enabled'] is True
    assert updated['display']['guide_message_enabled'] is True
    assert updated['display']['guide_message_text'] == 'TONIGHT\nClassic Horror Night'
    assert updated['pages'] == [{'keep': 'me'}]
    assert updated['theme'] == 'retrostation_mc'


def test_web_ui_has_live_guide_message_save_button():
    template = Path('app/templates/index.html').read_text(encoding='utf-8')
    assert 'Save Guide Message' in template
    assert '[blank:90]' in template
    assert '[message:45]' in template
    assert '[/message]' in template
    assert 'Enter Guide Channel message text here...' in template
    assert "formaction=\"{{ url_for('guide_message_settings') }}\"" in template
    assert 'no restart required' in template.lower()
