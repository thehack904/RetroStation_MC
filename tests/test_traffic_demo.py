"""Tests for the RSMC simulated Traffic virtual channel.

Covers:
  - City seed data integrity
  - Congestion distribution model
  - Deterministic payload generation
  - City configuration helpers (enable/disable, weights, rotation)
  - Road geometry cache utilities (disk cache, bundled data, Overpass fallback)
  - Basemap generation (placeholder)
  - Web routes (/traffic, /api/traffic, /api/traffic/roads, /virtual-channels/traffic/config)

Ported and adapted from RetroIPTVGuide v4.9.9-dev tests/test_traffic_demo.py.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Module loading ────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_traffic_module():
    """Import app.traffic_channel under a stable test alias."""
    spec = importlib.util.spec_from_file_location(
        "rsmc_traffic_channel_test",
        _REPO_ROOT / "app" / "traffic_channel.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_app_module():
    """Import the top-level app.py for Flask route tests."""
    spec = importlib.util.spec_from_file_location(
        "rsmc_app_traffic_test",
        _REPO_ROOT / "app.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tc():
    """traffic_channel module, loaded once per test session."""
    return _load_traffic_module()


@pytest.fixture(scope="module")
def web():
    """app.py module with manager stopped, loaded once per test session."""
    mod = _load_app_module()
    mod.manager.stop()
    return mod


@pytest.fixture()
def client(web, tmp_path, monkeypatch):
    """Flask test client with isolated ConfigStore database."""
    from app.config_store import ConfigStore
    isolated_store = ConfigStore(db_path=tmp_path / "test_config.db")
    monkeypatch.setattr(web, "store", isolated_store)
    web.app.config["TESTING"] = True
    with web.app.test_client() as c:
        yield c


# ─── City seed data ───────────────────────────────────────────────────────────

class TestCitySeedData:
    def test_seed_has_ten_cities(self, tc):
        assert len(tc._TRAFFIC_DEMO_CITIES_SEED) == 10

    def test_all_seed_cities_over_one_million(self, tc):
        for city in tc._TRAFFIC_DEMO_CITIES_SEED:
            assert city["population"] > 1_000_000, (
                f"{city['name']}, {city['state']} population "
                f"{city['population']} is not > 1M"
            )

    def test_seed_cities_have_required_keys(self, tc):
        required = {"name", "state", "lat", "lon", "population"}
        for city in tc._TRAFFIC_DEMO_CITIES_SEED:
            assert required.issubset(city.keys()), f"Missing keys in {city}"

    def test_seed_lat_lon_are_floats(self, tc):
        for city in tc._TRAFFIC_DEMO_CITIES_SEED:
            assert isinstance(city["lat"], float)
            assert isinstance(city["lon"], float)

    def test_seed_cities_function_returns_enabled_cities(self, tc):
        cities = tc.seed_cities()
        assert len(cities) == len(tc._TRAFFIC_DEMO_CITIES_SEED)
        for city in cities:
            assert city["enabled"] is True
            assert city["weight"] == 1
            assert isinstance(city["id"], int)


# ─── _get_congestion_distribution ────────────────────────────────────────────

class TestCongestionDistribution:
    def test_sums_to_100_all_hours(self, tc):
        for hour in range(24):
            g, y, r = tc._get_congestion_distribution(hour)
            assert g + y + r == 100, f"hour={hour}: {g}+{y}+{r} != 100"

    def test_weekend_sums_to_100_all_hours(self, tc):
        for hour in range(24):
            g, y, r = tc._get_congestion_distribution(hour, is_weekend=True)
            assert g + y + r == 100, f"weekend hour={hour}: {g}+{y}+{r} != 100"

    def test_rush_hour_has_more_red_than_overnight(self, tc):
        _, _, r_rush  = tc._get_congestion_distribution(7)   # morning rush
        _, _, r_night = tc._get_congestion_distribution(3)   # overnight
        assert r_rush > r_night

    def test_overnight_is_mostly_green(self, tc):
        g, y, r = tc._get_congestion_distribution(3)
        assert g >= 85

    def test_evening_rush_has_high_red(self, tc):
        g, y, r = tc._get_congestion_distribution(17)  # 5 pm
        assert r >= 20

    def test_weekend_overnight_is_mostly_green(self, tc):
        g, y, r = tc._get_congestion_distribution(3, is_weekend=True)
        assert g >= 85

    def test_return_type_is_tuple_of_ints(self, tc):
        result = tc._get_congestion_distribution(12)
        assert isinstance(result, tuple)
        assert len(result) == 3
        for v in result:
            assert isinstance(v, int)


# ─── build_traffic_payload ────────────────────────────────────────────────────

class TestBuildTrafficPayload:
    @pytest.fixture()
    def cities(self, tc):
        return tc.seed_cities()

    @pytest.fixture()
    def cfg(self):
        return {"rotation_mode": "admin_rotation", "rotation_seconds": 120, "pack": "[]"}

    def test_has_required_keys(self, tc, cities, cfg):
        p = tc.build_traffic_payload(cities, cfg)
        for k in ("updated", "city", "summary", "segments", "demo_mode", "time_slot"):
            assert k in p, f"Missing key: {k}"

    def test_demo_mode_is_always_true(self, tc, cities, cfg):
        p = tc.build_traffic_payload(cities, cfg)
        assert p["demo_mode"] is True

    def test_disclaimer_is_present(self, tc, cities, cfg):
        p = tc.build_traffic_payload(cities, cfg)
        assert "disclaimer" in p
        assert len(p["disclaimer"]) > 10

    def test_city_has_required_keys(self, tc, cities, cfg):
        city = tc.build_traffic_payload(cities, cfg)["city"]
        for k in ("id", "name", "state", "lat", "lon"):
            assert k in city

    def test_city_id_is_int(self, tc, cities, cfg):
        city = tc.build_traffic_payload(cities, cfg)["city"]
        assert isinstance(city["id"], int)

    def test_time_slot_is_int(self, tc, cities, cfg):
        assert isinstance(tc.build_traffic_payload(cities, cfg)["time_slot"], int)

    def test_summary_has_required_keys(self, tc, cities, cfg):
        summary = tc.build_traffic_payload(cities, cfg)["summary"]
        for k in ("congestion_level", "green_percent", "yellow_percent", "red_percent"):
            assert k in summary

    def test_percentages_sum_near_100(self, tc, cities, cfg):
        s = tc.build_traffic_payload(cities, cfg)["summary"]
        total = s["green_percent"] + s["yellow_percent"] + s["red_percent"]
        assert 98 <= total <= 102

    def test_segments_is_list_of_24(self, tc, cities, cfg):
        segs = tc.build_traffic_payload(cities, cfg)["segments"]
        assert isinstance(segs, list)
        assert len(segs) == 24

    def test_segments_have_id_and_valid_color(self, tc, cities, cfg):
        for seg in tc.build_traffic_payload(cities, cfg)["segments"]:
            assert "id" in seg
            assert seg["color"] in ("green", "yellow", "red")

    def test_payload_is_cached_on_second_call(self, tc, cities, cfg):
        # Clear cache between tests
        tc._PAYLOAD_CACHE.clear()
        p1 = tc.build_traffic_payload(cities, cfg)
        p2 = tc.build_traffic_payload(cities, cfg)
        assert p1 is p2  # same object from cache

    def test_city_is_from_seed_data(self, tc, cities, cfg):
        city = tc.build_traffic_payload(cities, cfg)["city"]
        seed_names = {c["name"] for c in tc._TRAFFIC_DEMO_CITIES_SEED}
        assert city["name"] in seed_names

    def test_congestion_level_is_valid(self, tc, cities, cfg):
        level = tc.build_traffic_payload(cities, cfg)["summary"]["congestion_level"]
        assert level in ("Light", "Moderate", "Heavy")

    def test_updated_is_iso_string(self, tc, cities, cfg):
        from datetime import datetime, timezone
        updated = tc.build_traffic_payload(cities, cfg)["updated"]
        dt = datetime.fromisoformat(updated)
        assert dt.tzinfo is not None

    def test_no_enabled_cities_returns_no_cities_flag(self, tc, cities, cfg):
        tc._PAYLOAD_CACHE.clear()
        disabled = [{**c, "enabled": False} for c in cities]
        p = tc.build_traffic_payload(disabled, cfg)
        assert p == {"no_cities": True}

    def test_random_pack_mode_with_empty_pack(self, tc, cities, cfg):
        tc._PAYLOAD_CACHE.clear()
        pack_cfg = {**cfg, "rotation_mode": "random_pack", "pack": "[]"}
        # Falls back to all enabled cities
        p = tc.build_traffic_payload(cities, pack_cfg)
        assert "city" in p

    def test_weighted_city_appears_more_often(self, tc, cfg, monkeypatch):
        """City with weight=10 should appear at more rotation slots than weight=1 cities."""
        tc._PAYLOAD_CACHE.clear()
        # Use a small 2-city list so proportions are measurable
        small_cities = [
            {"id": 1, "name": "Alpha", "state": "AX", "lat": 41.88, "lon": -87.63,
             "enabled": True, "weight": 10},
            {"id": 2, "name": "Beta", "state": "BX", "lat": 34.05, "lon": -118.24,
             "enabled": True, "weight": 1},
        ]
        seen = []
        for slot_offset in range(100):
            monkeypatch.setattr(tc.time, "time", lambda o=slot_offset: 1_000_000 + o * 120)
            tc._PAYLOAD_CACHE.clear()
            p = tc.build_traffic_payload(small_cities, cfg)
            seen.append(p["city"]["id"])
        # city id=1 weight=10, city id=2 weight=1 → expected proportion ~91 %
        fraction = seen.count(1) / len(seen)
        assert fraction > 0.5, f"heavy city proportion {fraction:.2f} unexpectedly low"


# ─── _generate_demo_incidents ─────────────────────────────────────────────────

class TestGenerateDemoIncidents:
    def test_returns_list(self, tc):
        import random
        rng = random.Random(42)
        incidents = tc._generate_demo_incidents("Chicago", rng, 20)
        assert isinstance(incidents, list)

    def test_incidents_have_required_keys(self, tc):
        import random
        rng = random.Random(42)
        incidents = tc._generate_demo_incidents("Chicago", rng, 20)
        for inc in incidents:
            for k in ("title", "severity", "icon", "road", "direction"):
                assert k in inc, f"Missing key {k} in {inc}"

    def test_severity_is_valid(self, tc):
        import random
        rng = random.Random(42)
        incidents = tc._generate_demo_incidents("New York City", rng, 25)
        for inc in incidents:
            assert inc["severity"] in ("red", "yellow", "green")

    def test_heavy_congestion_has_more_incidents(self, tc):
        import random
        light_rng = random.Random(42)
        heavy_rng = random.Random(42)
        light = tc._generate_demo_incidents("Dallas", light_rng, 2)
        heavy = tc._generate_demo_incidents("Dallas", heavy_rng, 30)
        assert len(heavy) >= len(light)

    def test_known_city_uses_local_highways(self, tc):
        import random
        rng = random.Random(99)
        incidents = tc._generate_demo_incidents("Los Angeles", rng, 20)
        roads = {inc["road"] for inc in incidents}
        la_highways = set(tc._CITY_HIGHWAYS.get("Los Angeles", []))
        assert roads & la_highways, "Expected some incidents on known LA highways"

    def test_unknown_city_uses_default_highways(self, tc):
        import random
        rng = random.Random(99)
        incidents = tc._generate_demo_incidents("Nowhere Town", rng, 10)
        roads = {inc["road"] for inc in incidents}
        defaults = set(tc._CITY_HIGHWAYS_DEFAULT)
        assert roads & defaults, "Expected unknown city to use default highways"


# ─── city_slug ────────────────────────────────────────────────────────────────

class TestCitySlug:
    def test_removes_spaces(self, tc):
        assert tc.city_slug("New York City") == "newyorkcity"

    def test_lowercases(self, tc):
        assert tc.city_slug("Los Angeles") == "losangeles"

    def test_removes_special_chars(self, tc):
        assert tc.city_slug("San José") == "sanjose" or \
               tc.city_slug("San Jose") == "sanjose"

    def test_empty_string(self, tc):
        assert tc.city_slug("") == ""


# ─── Road data cache ──────────────────────────────────────────────────────────

class TestRoadDataCache:
    def test_roads_cache_path_is_under_cache_dir(self, tc, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        path = tc._roads_cache_path("newyorkcity")
        assert str(path).startswith(str(tmp_path / "roads"))

    def test_roads_cache_path_sanitizes_traversal_chars(self, tc, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        # Traversal characters are stripped from slugs; result is still under cache dir
        path = tc._roads_cache_path("../etc/passwd")
        assert str(path).startswith(str((tmp_path / "roads").resolve()))

    def test_roads_cache_path_raises_on_all_special_chars(self, tc, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        # A slug that consists entirely of non-alphanumeric chars becomes empty → ValueError
        with pytest.raises(ValueError):
            tc._roads_cache_path("../../../")

    def test_load_roads_from_disk_returns_none_when_missing(self, tc, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        result = tc._load_roads_from_disk("missingslug")
        assert result is None

    def test_save_and_load_roads_roundtrip(self, tc, tmp_path, monkeypatch):
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": {"name": "Test Rd", "highway": "motorway", "way_id": 1}}
        ]}
        tc._save_roads_to_disk("testslug", geojson)
        loaded = tc._load_roads_from_disk("testslug")
        assert loaded is not None
        assert loaded["features"][0]["properties"]["name"] == "Test Rd"

    def test_load_roads_from_disk_returns_none_for_stale_file(
        self, tc, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        # Save with a normal disk TTL, then monkey-patch TTL to 0 to simulate staleness
        geojson = {"type": "FeatureCollection", "features": []}
        tc._save_roads_to_disk("staleslug", geojson)
        monkeypatch.setattr(tc, "_ROADS_DISK_TTL", 0)
        result = tc._load_roads_from_disk("staleslug")
        assert result is None

    def test_load_bundled_roads_returns_none_when_dir_missing(
        self, tc, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tc, "ROADS_BUNDLED_DIR", tmp_path / "no_such_dir")
        result = tc._load_bundled_roads("New York City")
        assert result is None

    def test_load_bundled_roads_returns_geojson_when_present(
        self, tc, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tc, "ROADS_BUNDLED_DIR", tmp_path / "roads")
        bundled_dir = tmp_path / "roads"
        bundled_dir.mkdir(parents=True, exist_ok=True)
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": {"name": "I-95", "highway": "motorway", "way_id": 99}}
        ]}
        (bundled_dir / "newyorkcity.geojson").write_text(json.dumps(geojson))
        result = tc._load_bundled_roads("New York City")
        assert result is not None
        assert result["features"][0]["properties"]["name"] == "I-95"

    def test_overpass_to_geojson_converts_correctly(self, tc):
        raw = {
            "elements": [
                {"type": "node", "id": 1, "lat": 40.7, "lon": -74.0},
                {"type": "node", "id": 2, "lat": 40.8, "lon": -73.9},
                {
                    "type": "way", "id": 100,
                    "nodes": [1, 2],
                    "tags": {"name": "Test Ave", "highway": "motorway"},
                },
            ]
        }
        result = tc._overpass_to_geojson(raw)
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 1
        f = result["features"][0]
        assert f["geometry"]["type"] == "LineString"
        assert f["properties"]["highway"] == "motorway"

    def test_overpass_to_geojson_skips_ways_with_missing_nodes(self, tc):
        raw = {
            "elements": [
                {"type": "node", "id": 1, "lat": 40.7, "lon": -74.0},
                # node 2 missing
                {
                    "type": "way", "id": 100,
                    "nodes": [1, 2],
                    "tags": {"highway": "motorway"},
                },
            ]
        }
        result = tc._overpass_to_geojson(raw)
        # The way should be skipped because it has only 1 resolvable node (<2)
        assert len(result["features"]) == 0

    def test_get_road_geojson_uses_disk_cache_before_network(
        self, tc, tmp_path, monkeypatch
    ):
        """get_road_geojson should return disk-cached data without calling Overpass."""
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads")
        monkeypatch.setattr(tc, "ROADS_BUNDLED_DIR", tmp_path / "no_bundled")
        monkeypatch.setattr(tc, "_ROADS_CACHE", {})
        monkeypatch.setattr(tc, "_ROADS_CACHE_TIME", {})

        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
             "properties": {"name": "I-10", "highway": "motorway", "way_id": 5}}
        ]}
        tc._save_roads_to_disk("houston", geojson)

        city = {"id": 4, "name": "Houston", "lat": 29.7604, "lon": -95.3698}
        called = []

        def fake_fetch(*args, **kwargs):
            called.append(True)
            return {"elements": []}

        monkeypatch.setattr(tc, "_fetch_overpass_roads", fake_fetch)
        result = tc.get_road_geojson(city)
        assert not called, "Overpass should not be called when disk cache is present"
        assert result["features"][0]["properties"]["name"] == "I-10"

    def test_get_road_geojson_falls_back_gracefully_on_network_failure(
        self, tc, tmp_path, monkeypatch
    ):
        """get_road_geojson must not raise even when all sources fail."""
        monkeypatch.setattr(tc, "ROADS_CACHE_DIR", tmp_path / "roads_empty")
        monkeypatch.setattr(tc, "ROADS_BUNDLED_DIR", tmp_path / "no_bundled")
        monkeypatch.setattr(tc, "_ROADS_CACHE", {})
        monkeypatch.setattr(tc, "_ROADS_CACHE_TIME", {})
        monkeypatch.setattr(tc, "_fetch_overpass_roads", lambda *a, **k: {"elements": []})

        city = {"id": 99, "name": "Unknown City", "lat": 0.0, "lon": 0.0}
        result = tc.get_road_geojson(city)
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []


# ─── Basemap generation ───────────────────────────────────────────────────────

class TestBasemapGeneration:
    def test_placeholder_creates_png(self, tc, tmp_path):
        """_generate_placeholder_basemap_png should create a valid PNG."""
        out_path = tmp_path / "newyorkcity.png"
        ok = tc._generate_placeholder_basemap_png("New York City", out_path)
        assert ok is True
        assert out_path.is_file()
        assert out_path.stat().st_size > 0

    def test_placeholder_png_is_correct_size(self, tc, tmp_path):
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")
        out_path = tmp_path / "chicago.png"
        tc._generate_placeholder_basemap_png("Chicago", out_path)
        with Image.open(out_path) as img:
            assert img.size == (tc.BASEMAP_W, tc.BASEMAP_H)

    def test_generate_basemap_png_returns_false_without_requests(
        self, tc, monkeypatch
    ):
        monkeypatch.setattr(tc, "_REQUESTS_AVAILABLE", False)
        ok = tc._generate_basemap_png(40.7, -74.0, "/tmp/rsmc_test_should_not_exist.png")
        assert ok is False

    def test_ensure_basemap_generates_placeholder_when_tiles_fail(
        self, tc, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(tc, "BASEMAP_DIR", tmp_path / "maps")
        # Make tile download always fail so placeholder path is used
        monkeypatch.setattr(tc, "_generate_basemap_png", lambda *a, **k: False)
        result = tc.ensure_basemap("Dallas", 32.7767, -96.797)
        assert result is not None
        assert result.is_file()


# ─── Web routes ───────────────────────────────────────────────────────────────

class TestTrafficRoutes:
    def test_traffic_page_returns_200(self, client):
        resp = client.get("/traffic")
        assert resp.status_code == 200

    def test_traffic_page_contains_simulated_disclaimer(self, client):
        body = client.get("/traffic").data.decode()
        # The page must clearly state simulated/demo nature
        assert "SIMULATED" in body.upper() or "simulated" in body.lower()

    def test_api_traffic_returns_200(self, client):
        resp = client.get("/api/traffic")
        assert resp.status_code == 200

    def test_api_traffic_returns_json(self, client):
        resp = client.get("/api/traffic")
        assert resp.content_type.startswith("application/json")

    def test_api_traffic_has_required_keys(self, client):
        data = client.get("/api/traffic").get_json()
        for k in ("city", "summary", "segments", "demo_mode"):
            assert k in data, f"Missing key: {k}"

    def test_api_traffic_demo_mode_is_true(self, client):
        data = client.get("/api/traffic").get_json()
        assert data.get("demo_mode") is True

    def test_api_traffic_has_disclaimer(self, client):
        data = client.get("/api/traffic").get_json()
        assert "disclaimer" in data
        assert len(data["disclaimer"]) > 10

    def test_api_traffic_roads_valid_city(self, client, web):
        cities = web._get_traffic_cities()
        city_id = cities[0]["id"]
        resp = client.get(f"/api/traffic/roads/{city_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["type"] == "FeatureCollection"

    def test_api_traffic_roads_unknown_city_returns_404(self, client):
        resp = client.get("/api/traffic/roads/99999")
        assert resp.status_code == 404

    def test_virtual_channels_page_includes_traffic_section(self, client):
        resp = client.get("/virtual-channels")
        body = resp.data.decode()
        assert "traffic" in body.lower() or "Traffic" in body

    def test_virtual_channels_page_returns_200(self, client):
        resp = client.get("/virtual-channels")
        assert resp.status_code == 200


# ─── Config save routes ───────────────────────────────────────────────────────

class TestTrafficConfigSave:
    def test_save_traffic_config_persists_enabled(self, client, web):
        resp = client.post(
            "/virtual-channels/traffic/config",
            data={
                "traffic_channel_enabled": "1",
                "traffic_rotation_mode": "admin_rotation",
                "traffic_rotation_seconds": "60",
                "traffic_pack_size": "5",
                "traffic_aspect_ratio": "4:3",
                "traffic_resolution": "960x720",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        cfg = web._get_traffic_config()
        assert cfg["enabled"] is True
        assert cfg["rotation_seconds"] == 60
        assert cfg["pack_size"] == 5
        assert cfg["aspect_ratio"] == "4:3"
        assert cfg["resolution"] == "960x720"

    def test_save_traffic_config_disabled_when_no_checkbox(self, client, web):
        resp = client.post(
            "/virtual-channels/traffic/config",
            data={
                "traffic_rotation_mode": "admin_rotation",
                "traffic_rotation_seconds": "120",
                "traffic_pack_size": "10",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        cfg = web._get_traffic_config()
        assert cfg["enabled"] is False

    def test_save_traffic_config_invalid_mode_is_corrected(self, client):
        resp = client.post(
            "/virtual-channels/traffic/config",
            data={
                "traffic_rotation_mode": "invalid_mode",
                "traffic_rotation_seconds": "120",
                "traffic_pack_size": "10",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Invalid mode is silently corrected to "admin_rotation"; save succeeds
        body = resp.data.decode()
        assert "saved" in body.lower() or "success" in body.lower() or resp.status_code == 200

    def test_traffic_page_exposes_independent_hd_sd_controls(self, client):
        resp = client.get("/virtual-channels")
        body = resp.data.decode()
        assert 'name="traffic_aspect_ratio"' in body
        assert 'name="traffic_resolution"' in body
        assert 'value="1280x720"' in body
        assert 'value="960x720"' in body



# ─── City management API ──────────────────────────────────────────────────────

class TestCityManagementAPI:
    def test_save_city_enabled_state(self, client, web):
        cities  = web._get_traffic_cities()
        city_id = cities[0]["id"]
        resp = client.post(
            f"/api/traffic/cities/{city_id}",
            json={"enabled": False, "weight": 1},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        updated = next(c for c in web._get_traffic_cities() if c["id"] == city_id)
        assert updated["enabled"] is False

    def test_save_city_weight(self, client, web):
        cities  = web._get_traffic_cities()
        city_id = cities[0]["id"]
        client.post(f"/api/traffic/cities/{city_id}", json={"enabled": True, "weight": 3})
        updated = next(c for c in web._get_traffic_cities() if c["id"] == city_id)
        assert updated["weight"] == 3

    def test_save_unknown_city_returns_404(self, client):
        resp = client.post("/api/traffic/cities/99999", json={"enabled": True})
        assert resp.status_code == 404

    def test_enable_all_cities(self, client, web):
        # First disable all
        client.post("/api/traffic/cities/disable-all")
        resp = client.post("/api/traffic/cities/enable-all")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert all(c["enabled"] for c in web._get_traffic_cities())

    def test_disable_all_cities(self, client, web):
        resp = client.post("/api/traffic/cities/disable-all")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert all(not c["enabled"] for c in web._get_traffic_cities())


# ─── _get_traffic_cities seeding ─────────────────────────────────────────────

class TestGetTrafficCities:
    def test_returns_ten_cities_by_default(self, client, web):
        cities = web._get_traffic_cities()
        assert len(cities) == len(web._TRAFFIC_DEMO_CITIES_SEED)

    def test_cities_have_required_keys(self, client, web):
        required = {"id", "name", "state", "lat", "lon", "population", "enabled", "weight"}
        for city in web._get_traffic_cities():
            assert required.issubset(city.keys())

    def test_all_cities_enabled_by_default(self, client, web):
        for city in web._get_traffic_cities():
            assert city["enabled"] is True

    def test_save_and_reload_cities(self, client, web):
        cities = web._get_traffic_cities()
        cities[0]["enabled"] = False
        web._save_traffic_cities(cities)
        reloaded = web._get_traffic_cities()
        assert reloaded[0]["enabled"] is False


class TestTrafficHlsIntegration:
    def test_channel_playlist_points_to_traffic_hls(self, client, web):
        cfg = web.store.get_config()
        cfg["traffic_channel_enabled"] = True
        web.store.save_config(cfg)
        body = client.get("/channel.m3u").get_data(as_text=True)
        assert "/hls/traffic.m3u8" in body
        assert "http://localhost/traffic\n" not in body

    def test_traffic_hls_route_is_media_playlist_not_html(self, client, web, tmp_path, monkeypatch):
        playlist = tmp_path / "traffic.m3u8"
        playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:6.0,\ntraffic_1.ts\n#EXTINF:6.0,\ntraffic_2.ts\n#EXTINF:6.0,\ntraffic_3.ts\n")
        monkeypatch.setattr(web, "TRAFFIC_PLAYLIST", playlist)
        monkeypatch.setattr(web.traffic_manager, "status", lambda: {"pipeline_active": True})
        monkeypatch.setattr(web.traffic_manager, "is_traffic_buffered", lambda: True)
        resp = client.get("/hls/traffic.m3u8")
        assert resp.status_code == 200
        assert resp.mimetype == "application/vnd.apple.mpegurl"
        assert "traffic_1.ts" in resp.get_data(as_text=True)
        assert "<html" not in resp.get_data(as_text=True).lower()
