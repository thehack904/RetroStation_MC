from pathlib import Path


def test_hdhomerun_use_and_rebroadcast_have_independent_bulk_controls():
    index = (Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html").read_text()
    assert "setHDHomeRunColumn('hdhomerun_testing_enabled_channels', true)" in index
    assert "setHDHomeRunColumn('hdhomerun_testing_enabled_channels', false)" in index
    assert "setHDHomeRunColumn('hdhomerun_testing_rebroadcast_channels', true)" in index
    assert "setHDHomeRunColumn('hdhomerun_testing_rebroadcast_channels', false)" in index
    assert "if (!box.disabled) box.checked = checked;" in index


def test_hdhomerun_active_column_enables_use_and_rebroadcast_non_destructively():
    index = (Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html").read_text()
    assert '<th>Use</th><th>Rebroadcast</th><th>Active</th>' in index
    assert 'class="hdhr-active"' in index
    assert 'setHDHomeRunActive(true)' in index
    assert 'setHDHomeRunActive(false)' in index
    assert 'if (use && !use.disabled) use.checked = true;' in index
    assert 'if (reb && !reb.disabled) reb.checked = true;' in index
    # Clearing Active must not clear Use or Rebroadcast.
    active_fn = index[index.index('function setHDHomeRunActive'):index.index('function applyHDHomeRunActive')]
    assert 'use.checked = false' not in active_fn
    assert 'reb.checked = false' not in active_fn
