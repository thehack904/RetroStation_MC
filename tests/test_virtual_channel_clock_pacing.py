from app.news_renderer import _advance_deadline as news_advance
from app.traffic_renderer import _advance_deadline as traffic_advance
from app.weather_renderer import _advance_deadline as weather_advance


def test_traffic_uses_weather_deadline_semantics():
    cases = [
        (10.0, 0.1, 9.95),
        (10.0, 0.1, 10.05),
        (10.0, 0.1, 10.35),
        (50.0, 1 / 15, 50.7),
    ]
    for args in cases:
        assert traffic_advance(*args) == weather_advance(*args)


def test_news_uses_weather_deadline_semantics():
    cases = [
        (10.0, 0.1, 9.95),
        (10.0, 0.1, 10.05),
        (10.0, 0.1, 10.35),
        (50.0, 1 / 15, 50.7),
    ]
    for args in cases:
        assert news_advance(*args) == weather_advance(*args)
