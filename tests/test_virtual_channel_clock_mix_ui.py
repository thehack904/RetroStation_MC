from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_traffic_renderer_has_live_clock():
    text=(ROOT/'app/traffic_renderer.py').read_text()
    assert "%I:%M:%S %p" in text
    assert "browser_timezone" in text
    assert "second != self._clock_second" in text

def test_news_renderer_has_live_clock():
    text=(ROOT/'app/news_renderer.py').read_text()
    assert "%I:%M:%S %p" in text
    assert "browser_timezone" in text
    assert "UPDATED:" in text

def test_mix_minutes_follows_input():
    text=(ROOT/'app/templates/virtual_channels.html').read_text()
    assert 'name="channel_mix_duration" min="1" max="1440" value="{{ entry.duration_minutes }}"><span class="mix-duration-unit">Minutes</span>' in text
    assert '<label>Minutes <input type="number" name="channel_mix_duration"' not in text

def test_mix_has_no_own_clock_markup():
    text=(ROOT/'app/templates/virtual_channels.html').read_text()
    mix=text.split('id="channel-mix-card"',1)[1]
    assert 'mix-clock' not in mix
