from __future__ import annotations

import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from werkzeug.datastructures import MultiDict


def _load_app_module():
    repo_root = Path(__file__).resolve().parents[1]
    app_path = repo_root / "app.py"
    spec = importlib.util.spec_from_file_location("retro_station_mc_hdhomerun", app_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load app.py module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HDHomeRunEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.web = _load_app_module()
        cls.web.manager.stop()
        cls.client = cls.web.app.test_client()

    def setUp(self) -> None:
        self.web.store.save_config(
            {
                "hdhomerun_enabled": False,
                "hdhomerun_device_id": "",
                "playlist_source": str(Path(__file__).resolve().parents[1] / "sample_data" / "channels.m3u"),
                "hdhomerun_rebroadcast_channels": [],
            }
        )

    def test_index_page_has_hdhomerun_toggle(self) -> None:
        body = self.client.get("/").data.decode()
        self.assertIn('name="hdhomerun_enabled"', body)
        self.assertIn("Emulate an HDHomeRun tuner for Plex and compatible DVR clients", body)
        self.assertIn("Imported source-playlist channels are not rebroadcast unless selected below", body)
        self.assertIn('name="hdhomerun_rebroadcast_channels"', body)

    def test_main_config_save_uses_checkbox_list_for_hdhomerun_mode(self) -> None:
        resp = self.client.post(
            "/config",
            data=MultiDict(
                [
                    ("hdhomerun_enabled", "0"),
                    ("hdhomerun_enabled", "1"),
                    ("action", "save"),
                ]
            ),
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.web.store.get_config().get("hdhomerun_enabled"))

    def test_hdhomerun_endpoints_return_404_when_disabled(self) -> None:
        self.assertEqual(self.client.get("/discover.json").status_code, 404)
        self.assertEqual(self.client.get("/lineup.json").status_code, 404)

    def test_discover_device_and_lineup_routes_are_available_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            playlist_path = Path(tmp_dir) / "channels.m3u"
            playlist_path.write_text(
                "#EXTM3U\n"
                '#EXTINF:-1 tvg-id="alpha" tvg-name="Alpha" tvg-chno="101",Alpha\n'
                "http://streams.example.test/alpha.m3u8\n"
                '#EXTINF:-1 tvg-id="beta" tvg-name="Beta" tvg-chno="202",Beta\n'
                "http://streams.example.test/beta.m3u8\n",
                encoding="utf-8",
            )
            source_channels = self.web.parse_m3u(str(playlist_path))
            beta_key = self.web._source_channel_key(source_channels[1], 1)
            self.web.store.save_config(
                {
                    "hdhomerun_enabled": True,
                    "hdhomerun_device_id": "12345678-1234-5678-1234-567812345678",
                    "playlist_source": str(playlist_path),
                    "hdhomerun_rebroadcast_channels": [beta_key],
                    "weather_channel_enabled": False,
                }
            )

            discover = self.client.get("/discover.json")
            self.assertEqual(discover.status_code, 200)
            payload = discover.get_json()
            self.assertEqual(payload["BaseURL"], "http://localhost")
            self.assertEqual(payload["LineupURL"], "http://localhost/lineup.json")
            self.assertEqual(payload["TunerCount"], 2)
            self.assertRegex(payload["DeviceID"], r"^[0-9A-F]{8}$")
            self.assertTrue(self.web.normalize_or_generate_device_id(payload["DeviceID"]) == payload["DeviceID"])

            device = self.client.get("/device.xml")
            self.assertEqual(device.status_code, 200)
            root = ET.fromstring(device.data.decode())
            ns = {"upnp": "urn:schemas-upnp-org:device-1-0"}
            self.assertEqual(root.findtext("upnp:URLBase", namespaces=ns), "http://localhost")
            self.assertEqual(root.findtext("upnp:device/upnp:friendlyName", namespaces=ns), "RetroStation MC")

            lineup = self.client.get("/lineup.json")
            self.assertEqual(lineup.status_code, 200)
            self.assertEqual(
                lineup.get_json(),
                [
                    {
                        "GuideNumber": "1",
                        "GuideName": "Guide Channel",
                        "URL": "http://localhost/hdhr/channel/0",
                    },
                    {
                        "GuideNumber": "202",
                        "GuideName": "Beta",
                        "URL": "http://localhost/hdhr/channel/1",
                    },
                ],
            )

            tune_head = self.client.head("/hdhr/channel/1")
            self.assertEqual(tune_head.status_code, 200)
            self.assertEqual(tune_head.mimetype, "video/mp2t")
            self.assertEqual(tune_head.headers.get("X-Accel-Buffering"), "no")


    def test_hdhomerun_tuner_uses_copy_remux_to_mpegts(self) -> None:
        command = self.web._build_hdhomerun_ffmpeg_command("http://example.test/live.m3u8")
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("http://example.test/live.m3u8", command)
        self.assertIn("copy", command)
        self.assertIn("mpegts", command)
        self.assertEqual(command[-1], "pipe:1")

    def test_hdhomerun_defaults_to_rsmc_owned_channels_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            playlist_path = Path(tmp_dir) / "channels.m3u"
            playlist_path.write_text(
                "#EXTM3U\n"
                '#EXTINF:-1 tvg-id="alpha" tvg-name="Alpha" tvg-chno="101",Alpha\n'
                "http://streams.example.test/alpha.m3u8\n",
                encoding="utf-8",
            )
            self.web.store.save_config({
                "hdhomerun_enabled": True,
                "playlist_source": str(playlist_path),
                "hdhomerun_rebroadcast_channels": [],
                "weather_channel_enabled": True,
                "weather_location_name": "Test City",
            })
            lineup = self.client.get("/lineup.json").get_json()
            self.assertEqual([item["GuideName"] for item in lineup], ["Guide Channel", "Weather Channel - Test City"])
            self.assertNotIn("Alpha", [item["GuideName"] for item in lineup])

    def test_hdhomerun_xmltv_matches_effective_lineup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            playlist_path = Path(tmp_dir) / "channels.m3u"
            playlist_path.write_text(
                "#EXTM3U\n"
                '#EXTINF:-1 tvg-id="alpha" tvg-name="Alpha" tvg-chno="101",Alpha\n'
                "http://streams.example.test/alpha.m3u8\n"
                '#EXTINF:-1 tvg-id="beta" tvg-name="Beta" tvg-chno="202",Beta\n'
                "http://streams.example.test/beta.m3u8\n",
                encoding="utf-8",
            )
            xmltv_path = Path(tmp_dir) / "guide.xml"
            xmltv_path.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<tv><channel id="beta"><display-name>Beta</display-name></channel>'
                '<programme start="20260810180000 +0000" stop="20260810190000 +0000" channel="beta">'
                '<title>Beta Show</title></programme></tv>',
                encoding="utf-8",
            )
            source_channels = self.web.parse_m3u(str(playlist_path))
            beta_key = self.web._source_channel_key(source_channels[1], 1)
            self.web.store.save_config({
                "hdhomerun_enabled": True,
                "playlist_source": str(playlist_path),
                "xmltv_source": str(xmltv_path),
                "hdhomerun_rebroadcast_channels": [beta_key],
                "weather_channel_enabled": True,
                "weather_location_name": "Test City",
            })

            lineup = self.client.get("/lineup.json").get_json()
            guide = self.client.get("/hdhr/guide.xml")
            self.assertEqual(guide.status_code, 200)
            root = ET.fromstring(guide.data.decode())
            xmltv_ids = [elem.attrib["id"] for elem in root.findall("channel")]
            self.assertEqual(xmltv_ids, ["rsmc-guide", "rsmc-weather", "beta"])
            self.assertEqual(len(xmltv_ids), len(lineup))
            self.assertNotIn("alpha", xmltv_ids)
            beta_titles = [
                elem.findtext("title")
                for elem in root.findall("programme")
                if elem.attrib.get("channel") == "beta"
            ]
            self.assertIn("Beta Show", beta_titles)

            alias = self.client.get("/hdhr/xmltv.xml")
            self.assertEqual(alias.status_code, 200)
            self.assertEqual(alias.data, guide.data)

    def test_hdhomerun_xmltv_is_disabled_with_tuner(self) -> None:
        self.assertEqual(self.client.get("/hdhr/guide.xml").status_code, 404)

    def test_config_saves_source_rebroadcast_selections(self) -> None:
        response = self.client.post(
            "/config",
            data=MultiDict([
                ("hdhomerun_enabled", "0"),
                ("hdhomerun_enabled", "1"),
                ("hdhomerun_rebroadcast_channels", "abc123"),
                ("hdhomerun_rebroadcast_channels", "def456"),
                ("action", "save"),
            ]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.web.store.get_config()["hdhomerun_rebroadcast_channels"], ["abc123", "def456"])

    def test_lineup_omits_selected_non_http_stream_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            playlist_path = Path(tmp_dir) / "channels.m3u"
            playlist_path.write_text(
                "#EXTM3U\n"
                '#EXTINF:-1 tvg-id="safe" tvg-name="Safe" tvg-chno="11",Safe\n'
                "https://streams.example.test/safe.m3u8\n"
                '#EXTINF:-1 tvg-id="unsafe" tvg-name="Unsafe" tvg-chno="12",Unsafe\n'
                "file:///tmp/unsafe.ts\n",
                encoding="utf-8",
            )
            parsed = self.web.parse_m3u(str(playlist_path))
            selected = [self.web._source_channel_key(channel, i) for i, channel in enumerate(parsed)]
            self.web.store.save_config(
                {
                    "hdhomerun_enabled": True,
                    "playlist_source": str(playlist_path),
                    "hdhomerun_rebroadcast_channels": selected,
                    "weather_channel_enabled": False,
                }
            )

            lineup = self.client.get("/lineup.json").get_json()
            self.assertEqual([item["GuideName"] for item in lineup], ["Guide Channel", "Safe"])
            tune = self.client.get("/hdhr/channel/1", follow_redirects=False)
            self.assertEqual(tune.status_code, 302)
            self.assertEqual(tune.headers["Location"], "https://streams.example.test/safe.m3u8")
            self.assertEqual(self.client.get("/hdhr/channel/2").status_code, 404)


if __name__ == "__main__":
    unittest.main()
