from pathlib import Path


def test_pipeline_status_is_collapsed_accordion_by_default():
    html = Path('app/templates/index.html').read_text(encoding='utf-8')
    assert 'class="section pipeline-status-accordion"' in html
    assert 'class="section__header pipeline-status-accordion__head"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="pipeline-status-body"' in html
    assert 'id="pipeline-status-body" class="pipeline-status-accordion__body" hidden' in html
    assert 'pipeline-status-accordion__toggle' in html
    assert '&#9650;' in html
    assert "body.hidden = expanded;" in html


def test_pipeline_status_arrow_semantics_match_guide_render_settings():
    html = Path('app/templates/index.html').read_text(encoding='utf-8')
    assert '.pipeline-status-accordion__head[aria-expanded="true"] .pipeline-status-accordion__toggle { transform: rotate(180deg); }' in html
    assert 'Pipeline Status accordion: collapsed by default; ▲ collapsed / ▼ expanded.' in html
