from pathlib import Path

from app.hdhomerun_hls import build_hdhomerun_hls_command
from app.hdhomerun_source import (
    apply_xmltv_channel_metadata,
    fetch_silicondust_xmltv_channels,
    merge_lineup_channels,
    normalize_base_url,
)


def test_normalize_base_url_accepts_hostname():
    assert normalize_base_url("hdhr-12345678.lan") == "http://hdhr-12345678.lan"


def test_merge_lineup_preserves_enabled_selection():
    lineup = [{
        "GuideNumber": "4.1",
        "GuideName": "KDFW-DT",
        "VideoCodec": "MPEG2",
        "AudioCodec": "AC3",
        "HD": 1,
        "URL": "http://hdhr/auto/v4.1",
    }]
    first = merge_lineup_channels(lineup)
    assert first[0]["enabled"] is True
    first[0]["enabled"] = False
    second = merge_lineup_channels(lineup, existing=first)
    assert second[0]["enabled"] is False
    assert second[0]["GuideNumber"] == "4.1"
    assert second[0]["GuideName"] == "KDFW-DT"


def test_hls_command_matches_validated_transport_shape(tmp_path: Path):
    cmd = build_hdhomerun_hls_command(
        "http://hdhr:5004/auto/v4.1",
        tmp_path / "test.m3u8",
        tmp_path / "test_%d.ts",
        use_vaapi=False,
    )
    joined = " ".join(cmd)
    assert "-c:v libx264" in joined
    assert "-c:a aac" in joined
    assert "-hls_time 6" in joined
    assert "-hls_segment_type mpegts" in joined
    assert "-hls_list_size 10" in joined


def test_hls_command_can_use_vaapi(tmp_path: Path):
    cmd = build_hdhomerun_hls_command(
        "http://hdhr:5004/auto/v4.1",
        tmp_path / "test.m3u8",
        tmp_path / "test_%d.ts",
        use_vaapi=True,
        vaapi_device="/dev/dri/renderD128",
        source_profile={"codec": "mpeg2video", "width": 704, "height": 480, "field_order": "tt", "interlaced": True, "fps": 30000/1001},
    )
    joined = " ".join(cmd)
    assert "-vaapi_device /dev/dri/renderD128" in joined
    assert "-c:v h264_vaapi" in joined
    assert "yadif=mode=send_field:parity=auto:deint=all,scale=720:480,format=nv12,hwupload" in joined
    assert "-r 60000/1001 -fps_mode cfr" in joined
    assert "-af aresample=async=1:first_pts=0" in joined


def test_silicondust_xmltv_channel_icon_mapping(monkeypatch):
    xml = """<?xml version='1.0' encoding='UTF-8'?>
    <tv>
      <channel id='US117086.hdhomerun.com'>
        <display-name>11.4 OUTLAW</display-name>
        <display-name>OUTLAW</display-name>
        <lcn>11.4</lcn>
        <icon src='https://img.hdhomerun.com/channels/US117086.png' width='360' height='270'/>
      </channel>
    </tv>
    """
    monkeypatch.setattr('app.hdhomerun_source.read_text_or_file', lambda source, timeout=12: xml)
    metadata = fetch_silicondust_xmltv_channels('test-auth')
    assert metadata['11.4']['xmltv_id'] == 'US117086.hdhomerun.com'
    assert metadata['11.4']['icon'] == 'https://img.hdhomerun.com/channels/US117086.png'


def test_apply_xmltv_metadata_flows_into_merged_channel():
    lineup = [{
        'GuideNumber': '11.4', 'GuideName': 'Outlaw', 'VideoCodec': 'MPEG2',
        'AudioCodec': 'AC3', 'URL': 'http://hdhr:5004/auto/v11.4',
    }]
    enriched = apply_xmltv_channel_metadata(lineup, {
        '11.4': {
            'xmltv_id': 'US117086.hdhomerun.com',
            'icon': 'https://img.hdhomerun.com/channels/US117086.png',
            'name': 'OUTLAW',
        }
    })
    merged = merge_lineup_channels(enriched)
    assert merged[0]['XMLTVID'] == 'US117086.hdhomerun.com'
    assert merged[0]['logo'] == 'https://img.hdhomerun.com/channels/US117086.png'
