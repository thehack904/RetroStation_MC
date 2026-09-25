import threading
import time

import pytest

from app.hdhomerun_shared import HDHomeRunSharedSourceManager


class _FakeResponse:
    def __init__(self, url, counter, stop_after=None):
        self.url = url
        self.counter = counter
        self.closed = False
        self.stop_after = stop_after

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        payload = (b'G' * 188) * 8
        sent = 0
        while not self.closed:
            yield payload
            sent += 1
            if self.stop_after and sent >= self.stop_after:
                return
            time.sleep(0.005)

    def close(self):
        self.closed = True


def _config(tuners=2, idle=1):
    return {
        'hdhomerun_testing_device': {'DeviceID': 'ABC12345', 'TunerCount': tuners},
        'hdhomerun_testing_idle_timeout_secs': idle,
    }


def _channel(key='ch71', number='7.1'):
    return {'key': key, 'GuideNumber': number, 'GuideName': f'CH {number}', 'URL': f'http://tuner/auto/v{number}'}


def test_two_consumers_same_channel_share_one_physical_request(monkeypatch):
    manager = HDHomeRunSharedSourceManager()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(url, calls)

    monkeypatch.setattr('app.hdhomerun_shared.requests.get', fake_get)
    registry, _ = manager.register(_config(), _channel())
    token1, q1 = manager.subscribe_registry(registry)
    token2, q2 = manager.subscribe_registry(registry)
    assert q1.get(timeout=1)
    assert q2.get(timeout=1)
    assert calls == ['http://tuner/auto/v7.1']
    manager.unsubscribe_registry(registry, token1)
    manager.unsubscribe_registry(registry, token2)
    manager.stop_all()


def test_different_channels_use_separate_physical_requests(monkeypatch):
    manager = HDHomeRunSharedSourceManager()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(url, calls)

    monkeypatch.setattr('app.hdhomerun_shared.requests.get', fake_get)
    r1, _ = manager.register(_config(2), _channel('ch71', '7.1'))
    r2, _ = manager.register(_config(2), _channel('ch121', '12.1'))
    t1, q1 = manager.subscribe_registry(r1)
    t2, q2 = manager.subscribe_registry(r2)
    assert q1.get(timeout=1)
    assert q2.get(timeout=1)
    assert sorted(calls) == ['http://tuner/auto/v12.1', 'http://tuner/auto/v7.1']
    manager.unsubscribe_registry(r1, t1)
    manager.unsubscribe_registry(r2, t2)
    manager.stop_all()


def test_third_distinct_channel_respects_two_tuner_capacity(monkeypatch):
    manager = HDHomeRunSharedSourceManager()

    monkeypatch.setattr(
        'app.hdhomerun_shared.requests.get',
        lambda url, **kwargs: _FakeResponse(url, []),
    )
    cfg = _config(2)
    r1, _ = manager.register(cfg, _channel('ch71', '7.1'))
    r2, _ = manager.register(cfg, _channel('ch121', '12.1'))
    r3, _ = manager.register(cfg, _channel('ch51', '5.1'))
    t1, q1 = manager.subscribe_registry(r1)
    t2, q2 = manager.subscribe_registry(r2)
    assert q1.get(timeout=1)
    assert q2.get(timeout=1)
    with pytest.raises(RuntimeError, match='All 2 HDHomeRun tuner'):
        manager.subscribe_registry(r3)
    manager.unsubscribe_registry(r1, t1)
    manager.unsubscribe_registry(r2, t2)
    manager.stop_all()


def test_warm_idle_session_is_reused_before_timeout(monkeypatch):
    manager = HDHomeRunSharedSourceManager()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(url, calls)

    monkeypatch.setattr('app.hdhomerun_shared.requests.get', fake_get)
    registry, _ = manager.register(_config(2, idle=2), _channel())
    token1, q1 = manager.subscribe_registry(registry)
    assert q1.get(timeout=1)
    manager.unsubscribe_registry(registry, token1)
    time.sleep(0.1)
    token2, q2 = manager.subscribe_registry(registry)
    assert q2.get(timeout=1)
    assert len(calls) == 1
    manager.unsubscribe_registry(registry, token2)
    manager.stop_all()


def test_late_joiner_receives_rolling_prebuffer(monkeypatch):
    manager = HDHomeRunSharedSourceManager()
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _FakeResponse(url, calls)

    monkeypatch.setattr('app.hdhomerun_shared.requests.get', fake_get)
    registry, _ = manager.register(_config(2, idle=2), _channel())
    token1, q1 = manager.subscribe_registry(registry)
    first = q1.get(timeout=1)
    assert first and first[0] == 0x47 and len(first) % 188 == 0
    # Let the shared source build some history before the late join.
    time.sleep(0.05)
    token2, q2 = manager.subscribe_registry(registry)
    seeded = q2.get(timeout=1)
    assert seeded and seeded[0] == 0x47 and len(seeded) % 188 == 0
    assert len(calls) == 1
    manager.unsubscribe_registry(registry, token1)
    manager.unsubscribe_registry(registry, token2)
    manager.stop_all()


def test_shared_relay_realigns_non_packet_http_chunks(monkeypatch):
    manager = HDHomeRunSharedSourceManager()

    class _OddChunkResponse(_FakeResponse):
        def iter_content(self, chunk_size):
            packet = b'G' + (b'X' * 187)
            payload = packet * 20
            # Deliberately split inside TS packets.
            pieces = [payload[:317], payload[317:911], payload[911:2049], payload[2049:]]
            for piece in pieces:
                if self.closed:
                    return
                yield piece
                time.sleep(0.005)
            while not self.closed:
                yield packet * 4
                time.sleep(0.005)

    monkeypatch.setattr(
        'app.hdhomerun_shared.requests.get',
        lambda url, **kwargs: _OddChunkResponse(url, []),
    )
    registry, _ = manager.register(_config(), _channel())
    token, q = manager.subscribe_registry(registry)
    block = q.get(timeout=1)
    assert block[0] == 0x47
    assert len(block) % 188 == 0
    for offset in range(0, len(block), 188):
        assert block[offset] == 0x47
    manager.unsubscribe_registry(registry, token)
    manager.stop_all()
