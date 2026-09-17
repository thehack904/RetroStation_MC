from __future__ import annotations

import binascii
import logging
import os
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable

HDHOMERUN_DISCOVER_UDP_PORT = 65001
HDHOMERUN_TYPE_DISCOVER_REQ = 0x0002
HDHOMERUN_TYPE_DISCOVER_RPY = 0x0003
HDHOMERUN_TAG_DEVICE_TYPE = 0x01
HDHOMERUN_TAG_DEVICE_ID = 0x02
HDHOMERUN_TAG_TUNER_COUNT = 0x10
HDHOMERUN_TAG_LINEUP_URL = 0x27
HDHOMERUN_TAG_BASE_URL = 0x2A
HDHOMERUN_TAG_MULTI_TYPE = 0x2D
HDHOMERUN_DEVICE_TYPE_WILDCARD = 0xFFFFFFFF
HDHOMERUN_DEVICE_TYPE_TUNER = 0x00000001
HDHOMERUN_DEVICE_ID_WILDCARD = 0xFFFFFFFF
HDHOMERUN_MAX_PACKET_SIZE = 1460

_CHECKSUM_LOOKUP = (0xA, 0x5, 0xF, 0x6, 0x7, 0xC, 0x1, 0xB, 0x9, 0x2, 0x8, 0xD, 0x4, 0x3, 0xE, 0x0)


def validate_device_id(device_id: int) -> bool:
    """Return True when *device_id* satisfies SiliconDust's DeviceID checksum."""
    if device_id in (0, HDHOMERUN_DEVICE_ID_WILDCARD):
        return device_id == HDHOMERUN_DEVICE_ID_WILDCARD
    checksum = 0
    checksum ^= _CHECKSUM_LOOKUP[(device_id >> 28) & 0x0F]
    checksum ^= (device_id >> 24) & 0x0F
    checksum ^= _CHECKSUM_LOOKUP[(device_id >> 20) & 0x0F]
    checksum ^= (device_id >> 16) & 0x0F
    checksum ^= _CHECKSUM_LOOKUP[(device_id >> 12) & 0x0F]
    checksum ^= (device_id >> 8) & 0x0F
    checksum ^= _CHECKSUM_LOOKUP[(device_id >> 4) & 0x0F]
    checksum ^= device_id & 0x0F
    return checksum == 0


def normalize_or_generate_device_id(raw: object) -> str:
    """Return a stable HDHomeRun-style 8-hex-character DeviceID when possible.

    Existing valid IDs are preserved. Older RSMC UUID values are migrated
    deterministically by using their hex digits as seed material and selecting
    a final checksum nibble accepted by libhdhomerun.
    """
    text = str(raw or "").strip().upper()
    compact = "".join(ch for ch in text if ch in "0123456789ABCDEF")

    if len(text) == 8:
        try:
            value = int(text, 16)
        except ValueError:
            value = 0
        if validate_device_id(value):
            return f"{value:08X}"

    # Keep the ID deterministic across upgrades when migrating the old UUID.
    # If there is no previous ID, os.urandom supplies a persistent seed once
    # the caller stores the generated result in configuration.
    if len(compact) >= 7:
        prefix = int(compact[:7], 16)
    else:
        prefix = int.from_bytes(os.urandom(4), "big") >> 4

    # Avoid reserved/all-zero-looking ranges while retaining deterministic
    # migration from an existing value.
    if prefix == 0 or prefix == 0x0FFFFFFF:
        prefix ^= 0x1310A5B

    for last_nibble in range(16):
        candidate = ((prefix & 0x0FFFFFFF) << 4) | last_nibble
        if validate_device_id(candidate):
            return f"{candidate:08X}"

    raise RuntimeError("Unable to generate a valid HDHomeRun DeviceID")


def _encode_var_length(length: int) -> bytes:
    if length < 0 or length > 0x3FFF:
        raise ValueError("HDHomeRun TLV length out of range")
    if length <= 127:
        return bytes((length,))
    return bytes(((length & 0x7F) | 0x80, (length >> 7) & 0xFF))


def _decode_var_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("truncated varlen")
    first = data[offset]
    offset += 1
    if first & 0x80:
        if offset >= len(data):
            raise ValueError("truncated varlen")
        return (first & 0x7F) | (data[offset] << 7), offset + 1
    return first, offset


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes((tag,)) + _encode_var_length(len(value)) + value


def _u32(value: int) -> bytes:
    return struct.pack(">I", value & 0xFFFFFFFF)


def build_frame(frame_type: int, payload: bytes) -> bytes:
    header_and_payload = struct.pack(">HH", frame_type, len(payload)) + payload
    crc = binascii.crc32(header_and_payload) & 0xFFFFFFFF
    return header_and_payload + struct.pack("<I", crc)


def parse_frame(packet: bytes) -> tuple[int, bytes]:
    if len(packet) < 8:
        raise ValueError("packet too short")
    frame_type, payload_length = struct.unpack(">HH", packet[:4])
    expected_length = 4 + payload_length + 4
    if len(packet) != expected_length:
        raise ValueError("packet length mismatch")
    expected_crc = struct.unpack("<I", packet[-4:])[0]
    actual_crc = binascii.crc32(packet[:-4]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise ValueError("packet CRC mismatch")
    return frame_type, packet[4:-4]


def parse_tlvs(payload: bytes) -> list[tuple[int, bytes]]:
    result: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(payload):
        tag = payload[offset]
        offset += 1
        length, offset = _decode_var_length(payload, offset)
        end = offset + length
        if end > len(payload):
            raise ValueError("truncated TLV")
        result.append((tag, payload[offset:end]))
        offset = end
    return result


def _requested_types_and_device_id(payload: bytes) -> tuple[set[int], int]:
    requested_types: set[int] = set()
    requested_device_id = HDHOMERUN_DEVICE_ID_WILDCARD
    for tag, value in parse_tlvs(payload):
        if tag == HDHOMERUN_TAG_DEVICE_TYPE and len(value) == 4:
            requested_types.add(struct.unpack(">I", value)[0])
        elif tag == HDHOMERUN_TAG_MULTI_TYPE and len(value) % 4 == 0:
            for offset in range(0, len(value), 4):
                requested_types.add(struct.unpack(">I", value[offset:offset + 4])[0])
        elif tag == HDHOMERUN_TAG_DEVICE_ID and len(value) == 4:
            requested_device_id = struct.unpack(">I", value)[0]
    if not requested_types:
        requested_types.add(HDHOMERUN_DEVICE_TYPE_WILDCARD)
    return requested_types, requested_device_id


def request_matches(packet: bytes, device_id: int) -> bool:
    try:
        frame_type, payload = parse_frame(packet)
        if frame_type != HDHOMERUN_TYPE_DISCOVER_REQ:
            return False
        requested_types, requested_device_id = _requested_types_and_device_id(payload)
    except (ValueError, struct.error):
        return False

    type_matches = (
        HDHOMERUN_DEVICE_TYPE_WILDCARD in requested_types
        or HDHOMERUN_DEVICE_TYPE_TUNER in requested_types
    )
    id_matches = requested_device_id in (HDHOMERUN_DEVICE_ID_WILDCARD, device_id)
    return type_matches and id_matches


def build_discovery_reply(device_id: int, tuner_count: int, base_url: str) -> bytes:
    lineup_url = f"{base_url.rstrip('/')}/lineup.json"
    payload = b"".join(
        (
            _tlv(HDHOMERUN_TAG_DEVICE_TYPE, _u32(HDHOMERUN_DEVICE_TYPE_TUNER)),
            _tlv(HDHOMERUN_TAG_DEVICE_ID, _u32(device_id)),
            _tlv(HDHOMERUN_TAG_TUNER_COUNT, bytes((max(1, min(int(tuner_count), 255)),))),
            _tlv(HDHOMERUN_TAG_BASE_URL, base_url.rstrip("/").encode("utf-8")),
            _tlv(HDHOMERUN_TAG_LINEUP_URL, lineup_url.encode("utf-8")),
        )
    )
    return build_frame(HDHOMERUN_TYPE_DISCOVER_RPY, payload)


def _local_ip_for_peer(peer_ip: str) -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((peer_ip, 9))
        return probe.getsockname()[0]
    finally:
        probe.close()


@dataclass
class HDHomeRunDiscoveryService:
    enabled_getter: Callable[[], bool]
    device_id_getter: Callable[[], str]
    tuner_count: int = 2
    http_port: int = 8787
    bind_address: str = "0.0.0.0"
    udp_port: int = HDHOMERUN_DISCOVER_UDP_PORT

    def __post_init__(self) -> None:
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self.last_error: str = ""

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._ready_event.clear()
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="hdhomerun-discovery", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=1.0)

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._socket = None

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((self.bind_address, self.udp_port))
            self.udp_port = sock.getsockname()[1]
            sock.settimeout(0.5)
            self._ready_event.set()
            logging.info("HDHomeRun discovery listening on UDP %s", self.udp_port)

            while not self._stop_event.is_set():
                try:
                    packet, peer = sock.recvfrom(HDHOMERUN_MAX_PACKET_SIZE)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise

                if not self.enabled_getter():
                    continue

                try:
                    device_id_text = self.device_id_getter()
                    device_id = int(device_id_text, 16)
                except Exception as exc:
                    logging.warning("HDHomeRun discovery skipped: unable to obtain a valid DeviceID: %s", exc)
                    continue

                if not request_matches(packet, device_id):
                    continue

                try:
                    local_ip = _local_ip_for_peer(peer[0])
                    base_url = f"http://{local_ip}:{self.http_port}"
                    reply = build_discovery_reply(device_id, self.tuner_count, base_url)
                    sock.sendto(reply, peer)
                    logging.debug("HDHomeRun discovery reply sent to %s:%s as %s", peer[0], peer[1], device_id_text)
                except OSError as exc:
                    logging.warning("HDHomeRun discovery reply failed for %s: %s", peer[0], exc)
        except OSError as exc:
            self.last_error = str(exc)
            self._ready_event.set()
            logging.error("HDHomeRun discovery could not bind UDP %s: %s", self.udp_port, exc)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._socket = None
