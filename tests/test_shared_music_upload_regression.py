from pathlib import Path


def test_shared_music_tab_redirect_and_request_limit_contract():
    source = Path('app.py').read_text()
    template = Path('app/templates/index.html').read_text()
    assert 'id="tab-music"' in template
    assert '#music-section' not in source
    assert 'url_for("index") + "#tab-music"' in source
    assert 'MAX_MUSIC_FILE_BYTES = 100 * 1024 * 1024' in source
    assert 'MAX_MUSIC_REQUEST_BYTES = 1024 * 1024 * 1024' in source
    assert 'app.config["MAX_CONTENT_LENGTH"] = MAX_MUSIC_REQUEST_BYTES' in source


def test_audio_validation_reads_header_only_and_enforces_file_size():
    source = Path('app.py').read_text()
    helper = source[source.index('def _is_audio_file'):source.index('def _save_uploaded_audio_files')]
    assert 'header = fh.read(16)' in helper
    assert 'path.read_bytes()' not in helper
    assert 'if size > MAX_MUSIC_FILE_BYTES:' in source
    assert '100 MB per-file limit' in source


def test_upload_ensures_shared_library_exists():
    source = Path('app.py').read_text()
    helper = source[source.index('def _save_uploaded_audio_files'):source.index('@app.post("/music/upload")')]
    assert 'destination_dir.mkdir(parents=True, exist_ok=True)' in helper
    assert 'Music library is not writable' in helper
