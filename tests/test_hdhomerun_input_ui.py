from pathlib import Path


def _index():
    return (Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html").read_text()


def test_hdhomerun_input_uses_compact_filterable_channel_table():
    index = _index()
    assert '>HDHomeRun Input</button>' in index
    assert 'id="hdhr-channel-search"' in index
    assert 'id="hdhr-channel-filter"' in index
    assert 'value="used">Used only' in index
    assert 'value="rebroadcast">Rebroadcast only' in index
    assert 'value="unused">Unused' in index
    assert 'class="hdhr-channel-scroll"' in index
    assert 'position:sticky' in index
    assert "' discovered • '" in index


def test_direct_tests_live_in_diagnostics_single_channel_view():
    index = _index()
    input_start = index.index('<!-- HDHomeRun Input Tab -->')
    input_end = index.index('<!-- About Tab -->', input_start)
    input_html = index[input_start:input_end]
    assert 'Direct Tests' not in input_html
    assert 'id="hdhr-diagnostic-channel"' in index
    assert 'class="hdhr-diagnostic-panel"' in index
    assert '<strong>Direct Tests</strong>' in index
    assert 'HDHomeRun Input → Use' in index
