from __future__ import annotations

import os
import socket
import unittest
from urllib.error import URLError
from unittest.mock import Mock, patch

from app.source_fetch import _parse_host_aliases, read_text


class _MockResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


class SourceFetchDnsFallbackTests(unittest.TestCase):
    def test_normal_url_fetch_works_without_fallback(self) -> None:
        with patch("app.source_fetch.urlopen", return_value=_MockResponse(b"ok")) as mocked_urlopen:
            result = read_text("http://example.com/channels.m3u", timeout=15)

        self.assertEqual("ok", result)
        self.assertEqual(1, mocked_urlopen.call_count)

    def test_parse_host_aliases_single_entry(self) -> None:
        aliases = _parse_host_aliases("iptv.lan=10.7.0.25")
        self.assertEqual({"iptv.lan": "10.7.0.25"}, aliases)

    def test_parse_host_aliases_multiple_entries(self) -> None:
        aliases = _parse_host_aliases("iptv.lan=10.7.0.25,epg.lan=10.7.0.26")
        self.assertEqual({"iptv.lan": "10.7.0.25", "epg.lan": "10.7.0.26"}, aliases)

    def test_parse_host_aliases_ignores_invalid_entries(self) -> None:
        logger = Mock()
        with patch("app.source_fetch._logger", logger):
            aliases = _parse_host_aliases("iptv.lan=10.7.0.25,invalid,nope=http://bad,=10.0.0.1,host=")
        self.assertEqual({"iptv.lan": "10.7.0.25"}, aliases)
        self.assertEqual(4, logger.warning.call_count)

    def test_dns_failure_retries_using_alias_target(self) -> None:
        dns_error = URLError(socket.gaierror(-5, "No address associated with hostname"))
        with patch.dict(os.environ, {"RETROGUIDE_HOST_ALIASES": "iptv.lan=10.7.0.25"}), patch(
            "app.source_fetch.urlopen",
            side_effect=[dns_error, _MockResponse(b"ok")],
        ) as mocked_urlopen:
            result = read_text("http://iptv.lan:8409/iptv/channels.m3u?view=full#frag", timeout=15)

        self.assertEqual("ok", result)
        self.assertEqual(2, mocked_urlopen.call_count)
        fallback_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual("http://10.7.0.25:8409/iptv/channels.m3u?view=full#frag", fallback_request.full_url)
        self.assertEqual("iptv.lan:8409", fallback_request.get_header("Host"))

    def test_dns_failure_alias_matching_is_case_insensitive(self) -> None:
        dns_error = URLError(socket.gaierror(-2, "Name or service not known"))
        with patch.dict(os.environ, {"RETROGUIDE_HOST_ALIASES": "IPTV.LAN=10.7.0.25"}), patch(
            "app.source_fetch.urlopen",
            side_effect=[dns_error, _MockResponse(b"ok")],
        ) as mocked_urlopen:
            read_text("http://iptv.lan/channels.m3u", timeout=15)

        fallback_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual("http://10.7.0.25/channels.m3u", fallback_request.full_url)
        self.assertEqual("iptv.lan", fallback_request.get_header("Host"))

    def test_dns_failure_without_alias_adds_actionable_resolution_message(self) -> None:
        dns_error = URLError(socket.gaierror(-5, "No address associated with hostname"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=False,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=dns_error,
        ):
            with self.assertRaises(URLError) as ctx:
                read_text("http://iptv.lan:8409/iptv/channels.m3u", timeout=15)

        message = str(ctx.exception)
        self.assertIn("No address associated with hostname", message)
        self.assertIn("Unable to resolve hostname 'iptv.lan' from inside the container", message)
        self.assertIn("RETROGUIDE_HOST_ALIASES=iptv.lan=<ip-address>", message)

    def test_dns_failure_in_docker_retries_via_host_docker_internal(self) -> None:
        dns_error = URLError(socket.gaierror(-2, "Name or service not known"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=True,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=[dns_error, _MockResponse(b"ok")],
        ) as mocked_urlopen:
            result = read_text("http://iptv.lan:8080/channels.m3u", timeout=15)

        self.assertEqual("ok", result)
        self.assertEqual(2, mocked_urlopen.call_count)
        fallback_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual("http://host.docker.internal:8080/channels.m3u", fallback_request.full_url)
        self.assertEqual("iptv.lan:8080", fallback_request.get_header("Host"))

    def test_dns_failure_outside_docker_does_not_retry(self) -> None:
        dns_error = URLError(socket.gaierror(-2, "Name or service not known"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=False,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=dns_error,
        ) as mocked_urlopen:
            with self.assertRaises(URLError):
                read_text("http://iptv.lan/channels.m3u", timeout=15)

        self.assertEqual(1, mocked_urlopen.call_count)

    def test_dns_failure_with_ip_source_does_not_retry(self) -> None:
        dns_error = URLError(socket.gaierror(-2, "Name or service not known"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=True,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=dns_error,
        ) as mocked_urlopen:
            with self.assertRaises(URLError):
                read_text("http://192.168.1.20/channels.m3u", timeout=15)

        self.assertEqual(1, mocked_urlopen.call_count)

    def test_localhost_connection_refused_in_docker_retries_via_host_docker_internal(self) -> None:
        refused_error = URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=True,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=[refused_error, _MockResponse(b"guide data")],
        ) as mocked_urlopen:
            result = read_text("http://localhost:9090/channels.m3u", timeout=15)

        self.assertEqual("guide data", result)
        self.assertEqual(2, mocked_urlopen.call_count)
        fallback_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual("http://host.docker.internal:9090/channels.m3u", fallback_request.full_url)
        self.assertEqual("localhost:9090", fallback_request.get_header("Host"))

    def test_localhost_connection_refused_outside_docker_does_not_retry(self) -> None:
        refused_error = URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=False,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=refused_error,
        ) as mocked_urlopen:
            with self.assertRaises(URLError):
                read_text("http://localhost:9090/channels.m3u", timeout=15)

        self.assertEqual(1, mocked_urlopen.call_count)

    def test_connection_refused_on_non_localhost_does_not_retry(self) -> None:
        refused_error = URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=True,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=refused_error,
        ) as mocked_urlopen:
            with self.assertRaises(URLError):
                read_text("http://iptv.lan/channels.m3u", timeout=15)

        self.assertEqual(1, mocked_urlopen.call_count)

    def test_fallback_failure_raises_original_error(self) -> None:
        original_dns_error = URLError(socket.gaierror(-2, "Name or service not known"))
        fallback_error = URLError(socket.gaierror(-2, "Name or service not known"))
        with patch.dict(os.environ, {}, clear=True), patch(
            "app.source_fetch._is_running_in_docker",
            return_value=True,
        ), patch(
            "app.source_fetch.urlopen",
            side_effect=[original_dns_error, fallback_error],
        ):
            with self.assertRaises(URLError) as ctx:
                read_text("http://iptv.lan/channels.m3u", timeout=15)

        self.assertIs(original_dns_error, ctx.exception)
        self.assertTrue(ctx.exception.__suppress_context__)


if __name__ == "__main__":
    unittest.main()
