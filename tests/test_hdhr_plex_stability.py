from pathlib import Path


def _root():
    return Path(__file__).resolve().parents[1]


def test_hdhr_tuner_uses_devnull_and_no_stderr_helper_thread():
    text = (_root() / 'app.py').read_text(encoding='utf-8')
    start = text.index('def _hdhomerun_mpegts_chunks')
    end = text.index('@app.route("/hdhr/channel/', start)
    block = text[start:end]
    assert 'stderr=subprocess.DEVNULL' in block
    assert 'hdhr-ffmpeg-stderr' not in block
    assert 'stderr=subprocess.PIPE' not in block


def test_logger_does_not_require_sqlite_write_to_succeed():
    text = (_root() / 'app' / 'logging_utils.py').read_text(encoding='utf-8')
    assert 'try:' in text
    assert 'self.store.add_event' in text
    assert 'event database write failed' in text
