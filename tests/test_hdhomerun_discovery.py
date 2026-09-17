from __future__ import annotations

import socket
import struct
import unittest

from app.hdhomerun_discovery import (
    HDHOMERUN_DEVICE_ID_WILDCARD,
    HDHOMERUN_DEVICE_TYPE_TUNER,
    HDHOMERUN_TAG_BASE_URL,
    HDHOMERUN_TAG_DEVICE_ID,
    HDHOMERUN_TAG_DEVICE_TYPE,
    HDHOMERUN_TAG_LINEUP_URL,
    HDHOMERUN_TAG_TUNER_COUNT,
    HDHOMERUN_TYPE_DISCOVER_REQ,
    HDHOMERUN_TYPE_DISCOVER_RPY,
    HDHomeRunDiscoveryService,
    _tlv,
    _u32,
    build_frame,
    normalize_or_generate_device_id,
    parse_frame,
    parse_tlvs,
    request_matches,
    validate_device_id,
)


class HDHomeRunDiscoveryProtocolTests(unittest.TestCase):
    def test_uuid_migrates_to_stable_valid_device_id(self) -> None:
        old = "93292306-7493-46af-8860-5bd302a4d369"
        first = normalize_or_generate_device_id(old)
        second = normalize_or_generate_device_id(old)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9A-F]{8}$")
        self.assertTrue(validate_device_id(int(first, 16)))

    def test_existing_valid_device_id_is_preserved(self) -> None:
        generated = normalize_or_generate_device_id("")
        self.assertEqual(normalize_or_generate_device_id(generated.lower()), generated)

    def test_discovery_request_matching(self) -> None:
        device_id = int(normalize_or_generate_device_id("12345678-1234-5678-1234-567812345678"), 16)
        payload = b"".join(
            (
                _tlv(HDHOMERUN_TAG_DEVICE_TYPE, _u32(HDHOMERUN_DEVICE_TYPE_TUNER)),
                _tlv(HDHOMERUN_TAG_DEVICE_ID, _u32(HDHOMERUN_DEVICE_ID_WILDCARD)),
            )
        )
        packet = build_frame(HDHOMERUN_TYPE_DISCOVER_REQ, payload)
        self.assertTrue(request_matches(packet, device_id))
        self.assertFalse(request_matches(packet[:-1] + b"\x00", device_id))

    def test_udp_service_replies_with_http_metadata(self) -> None:
        device_id_text = normalize_or_generate_device_id("ABCDEF01-2345-6789-ABCD-EF0123456789")
        service = HDHomeRunDiscoveryService(
            enabled_getter=lambda: True,
            device_id_getter=lambda: device_id_text,
            tuner_count=2,
            http_port=8787,
            bind_address="127.0.0.1",
            udp_port=0,
        )

        service.start()
        port = service.udp_port
        self.assertFalse(service.last_error)

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(2.0)
        try:
            request_payload = b"".join(
                (
                    _tlv(HDHOMERUN_TAG_DEVICE_TYPE, _u32(HDHOMERUN_DEVICE_TYPE_TUNER)),
                    _tlv(HDHOMERUN_TAG_DEVICE_ID, _u32(HDHOMERUN_DEVICE_ID_WILDCARD)),
                )
            )
            client.sendto(build_frame(HDHOMERUN_TYPE_DISCOVER_REQ, request_payload), ("127.0.0.1", port))
            packet, _ = client.recvfrom(1460)
            frame_type, payload = parse_frame(packet)
            self.assertEqual(frame_type, HDHOMERUN_TYPE_DISCOVER_RPY)
            tlvs = dict(parse_tlvs(payload))
            self.assertEqual(struct.unpack(">I", tlvs[HDHOMERUN_TAG_DEVICE_ID])[0], int(device_id_text, 16))
            self.assertEqual(struct.unpack(">I", tlvs[HDHOMERUN_TAG_DEVICE_TYPE])[0], HDHOMERUN_DEVICE_TYPE_TUNER)
            self.assertEqual(tlvs[HDHOMERUN_TAG_TUNER_COUNT], b"\x02")
            self.assertEqual(tlvs[HDHOMERUN_TAG_BASE_URL].decode(), "http://127.0.0.1:8787")
            self.assertEqual(tlvs[HDHOMERUN_TAG_LINEUP_URL].decode(), "http://127.0.0.1:8787/lineup.json")
        finally:
            client.close()
            service.stop()


if __name__ == "__main__":
    unittest.main()
