from __future__ import annotations
import pytest
from app.news_channel import parse_feed, get_current_feed_state, validate_feed_urls

RSS=br"""<rss version="2.0"><channel><title>Example News</title><item><title>Alpha &amp; Beta</title><link>https://example.com/a</link><pubDate>Tue</pubDate><description><![CDATA[<b>Summary</b> text]]></description></item></channel></rss>"""
ATOM=br"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom News</title><entry><title>Atom Headline</title><link href="https://example.com/atom"/><updated>2026-08-11T12:00:00Z</updated><summary>Atom summary</summary></entry></feed>"""

def test_parse_rss_normalizes_and_strips_html():
    items=parse_feed(RSS); assert items[0]['title']=='Alpha & Beta'; assert items[0]['source']=='Example News'; assert items[0]['summary']=='Summary text'

def test_parse_atom():
    item=parse_feed(ATOM)[0]; assert item['title']=='Atom Headline'; assert item['url']=='https://example.com/atom'

def test_malformed_feed_raises_parse_error():
    import xml.etree.ElementTree as ET
    with pytest.raises(ET.ParseError): parse_feed(b'<rss><broken>')

def test_feed_validation_six_limit_and_scheme():
    urls=[f'https://example.com/{i}.xml' for i in range(8)]; assert len(validate_feed_urls(urls))==6
    with pytest.raises(ValueError): validate_feed_urls(['ftp://example.com/feed'])

def test_synchronized_feed_slot():
    # six feeds = 300 second slots. Absolute wall clock determines index.
    assert get_current_feed_state(6, now=0)[0]==0
    assert get_current_feed_state(6, now=301)[0]==1
    assert get_current_feed_state(6, now=1801)[0]==0


def test_empty_rss_feed_returns_empty():
    assert parse_feed(b'<rss version="2.0"><channel><title>Empty</title></channel></rss>') == []

def test_fetch_cache_rate_limits(monkeypatch):
    from app import news_channel as nc
    nc.clear_feed_cache(); calls=[]
    class Resp:
        content=RSS
        def raise_for_status(self): pass
    def fake_get(*args, **kwargs): calls.append(1); return Resp()
    monkeypatch.setattr(nc.requests, 'get', fake_get)
    assert nc.fetch_feed('https://example.com/rss.xml')
    assert nc.fetch_feed('https://example.com/rss.xml')
    assert len(calls)==1

def test_unreachable_feed_degrades_to_empty(monkeypatch):
    from app import news_channel as nc
    nc.clear_feed_cache()
    def boom(*args, **kwargs): raise RuntimeError('network down')
    monkeypatch.setattr(nc.requests, 'get', boom)
    assert nc.fetch_feed('https://example.com/rss.xml') == []
