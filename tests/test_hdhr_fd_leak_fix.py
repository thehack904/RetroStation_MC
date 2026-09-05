from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app.py'
STORE = ROOT / 'app' / 'config_store.py'


def test_config_store_explicitly_closes_every_connection():
    text = STORE.read_text(encoding='utf-8')
    assert 'from contextlib import closing' in text
    assert 'with self._connect() as conn:' not in text
    assert text.count('with closing(self._connect()) as conn:') >= 8


def test_rsmc_hdhr_channels_use_local_hls_playlists():
    text = APP.read_text(encoding='utf-8')
    start = text.index('def _hdhomerun_local_playlist_for_channel')
    end = text.index('def _build_hdhomerun_ffmpeg_command', start)
    block = text[start:end]
    for playlist in ['guide.m3u8', 'weather.m3u8', 'traffic.m3u8', 'news.m3u8', 'CHANNEL_MIX_LOCAL_PLAYLIST']:
        assert playlist in block
    assert 'source_kind' in block


def test_hdhr_tune_uses_local_path_for_owned_channels():
    text = APP.read_text(encoding='utf-8')
    start = text.index('def hdhomerun_channel_tune')
    end = text.index('def _build_xmltv_content', start)
    block = text[start:end]
    assert '_hdhomerun_local_playlist_for_channel(channel)' in block
    assert 'tuner_input = str(local_playlist)' in block
    assert '_hdhomerun_mpegts_chunks(tuner_input, local_hls=(local_playlist is not None))' in block


def test_channel_mix_is_materialized_to_disk_for_local_hdhr_input():
    text = APP.read_text(encoding='utf-8')
    assert 'CHANNEL_MIX_LOCAL_PLAYLIST = "channel-mix.m3u8"' in text
    assert 'def _refresh_channel_mix_local_playlist' in text
    assert '_atomic_write_text(OUTPUT_DIR / CHANNEL_MIX_LOCAL_PLAYLIST, text)' in text
    assert 'name="channel-mix-refresh"' in text
