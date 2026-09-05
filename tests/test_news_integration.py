from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def _load_web():
    spec=importlib.util.spec_from_file_location("rsmc_news_web_test", ROOT/"app.py")
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    mod.manager.stop()
    if mod.weather_manager: mod.weather_manager.stop()
    if mod.traffic_manager: mod.traffic_manager.stop()
    if mod.news_manager: mod.news_manager.stop()
    return mod

@pytest.fixture(scope="module")
def web(): return _load_web()

def test_news_channel_entry_when_enabled(web):
    cfg={**web.DEFAULT_CONFIG,'news_channel_enabled':True}
    entries=web._build_virtual_channel_entries(cfg,'http://localhost')
    news=next(x for x in entries if x['id']==web.VIRTUAL_NEWS_CHANNEL_ID)
    assert news['channel_number']==4
    assert news['stream_url'].endswith('/hls/news.m3u8')

def test_news_admin_supports_six_slots(web):
    with web.app.test_client() as c: body=c.get('/virtual-channels').get_data(as_text=True)
    for i in range(1,7): assert f'name="news_feed_url_{i}"' in body

def test_news_api_unconfigured(web, tmp_path, monkeypatch):
    from app.config_store import ConfigStore
    isolated=ConfigStore(db_path=tmp_path/'news.db'); monkeypatch.setattr(web,'store',isolated)
    with web.app.test_client() as c: data=c.get('/api/news').get_json()
    assert data['configured'] is False and data['headlines']==[] and data['feed_count']==0

def test_m3u_news_is_hls_not_html(web):
    cfg={**web.DEFAULT_CONFIG,'news_channel_enabled':True}
    text=web._build_channels_m3u_content(web._build_virtual_channel_entries(cfg,'http://localhost'),'http://localhost/channel.xmltv')
    assert 'tvg-chno="4"' in text and '/hls/news.m3u8' in text
    assert '\nhttp://localhost/news\n' not in text
