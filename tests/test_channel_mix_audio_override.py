from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_mix_has_shared_music_controls():
    t=(ROOT/'app/templates/virtual_channels.html').read_text()
    assert 'channel_music("channel_mix_", channel_mix, music_files' in t
    assert 'Member-channel audio is always dropped' in t

def test_mix_audio_pipeline_maps_only_member_video():
    text=(ROOT/'app.py').read_text()
    assert 'key_prefix="channel_mix_music_"' in text
    assert '"-c:v", "copy", *audio_codec_args, *audio_map_args' in text
    assert 'CHANNEL_MIX_SOURCE_PLAYLIST = "channel-mix-source.m3u8"' in text
    assert 'channel_mix_audio_%d.ts' in text

def test_mix_music_defaults_exist():
    text=(ROOT/'app/config_store.py').read_text()
    for key in ('channel_mix_music_mode','channel_mix_music_loop','channel_mix_music_single_file','channel_mix_music_playlist_files'):
        assert f'"{key}"' in text
