"""Shared physical HDHomeRun source sessions.

Only this module opens a physical SiliconDust channel URL.  All RSMC consumers
(Guide Preview, HLS/MPEG-TS rebroadcast, raw passthrough, diagnostics) attach to
an internal localhost MPEG-TS relay so multiple consumers of the same station
use one tuner session.
"""
from __future__ import annotations

import queue
import threading
from collections import deque
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import quote, unquote, urlparse

import requests

_TS_PACKET_SIZE = 188
_CHUNK = _TS_PACKET_SIZE * 256
_QUEUE_CHUNKS = 128
_PREBUFFER_CHUNKS = 64


def _device_key(config: dict) -> str:
    device = config.get("hdhomerun_testing_device")
    if isinstance(device, dict):
        value = str(device.get("DeviceID") or device.get("BaseURL") or "").strip()
        if value:
            return value
    return str(config.get("hdhomerun_testing_host") or "hdhomerun").strip() or "hdhomerun"


def _tuner_count(config: dict) -> int:
    device = config.get("hdhomerun_testing_device")
    if not isinstance(device, dict):
        return 0
    try:
        return max(0, int(device.get("TunerCount") or 0))
    except (TypeError, ValueError):
        return 0


def _idle_timeout(config: dict) -> int:
    try:
        return max(5, min(3600, int(config.get("hdhomerun_testing_idle_timeout_secs") or 30)))
    except (TypeError, ValueError):
        return 30


@dataclass
class _SourceSession:
    manager: "HDHomeRunSharedSourceManager"
    registry_key: str
    device_key: str
    channel_key: str
    channel_name: str
    stream_url: str
    tuner_count: int
    idle_timeout_secs: int
    subscribers: dict[object, queue.Queue] = field(default_factory=dict)
    thread: threading.Thread | None = None
    upstream: requests.Response | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    ready_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    last_error: str = ""
    started_at: float = 0.0
    last_activity_at: float = 0.0
    idle_deadline: float | None = None
    bytes_read: int = 0
    prebuffer: deque[bytes] = field(default_factory=lambda: deque(maxlen=_PREBUFFER_CHUNKS))

    def is_running(self) -> bool:
        thread = self.thread
        return bool(thread and thread.is_alive() and not self.stop_event.is_set())

    def _start_locked(self) -> None:
        if self.is_running():
            return
        if not self.manager._capacity_available(self):
            raise RuntimeError(
                f"All {self.tuner_count} HDHomeRun tuner(s) are currently in use."
                if self.tuner_count else "HDHomeRun tuner capacity is unavailable."
            )
        self.stop_event.clear()
        self.ready_event.clear()
        self.last_error = ""
        self.started_at = time.time()
        self.last_activity_at = self.started_at
        self.idle_deadline = None
        self.thread = threading.Thread(
            target=self._run,
            name=f"hdhr-source-{self.channel_key[:12]}",
            daemon=True,
        )
        self.thread.start()

    def subscribe(self, *, startup_timeout: float = 8.0) -> tuple[object, queue.Queue]:
        token = object()
        subscriber_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_CHUNKS)
        with self.lock:
            # Adding a consumer cancels the warm-idle countdown.
            # If this is a late join to an already-running tuner session, seed
            # the subscriber with a short aligned rolling prebuffer.  FFmpeg
            # then sees recent MPEG-TS tables/codec headers before the live
            # edge instead of attaching at an arbitrary byte/PES boundary.
            was_running = self.is_running()
            self.idle_deadline = None
            self._start_locked()
            if was_running and self.prebuffer:
                for buffered in self.prebuffer:
                    try:
                        subscriber_queue.put_nowait(buffered)
                    except queue.Full:
                        break
            self.subscribers[token] = subscriber_queue
            self.last_activity_at = time.time()
        if not self.ready_event.wait(timeout=max(0.5, startup_timeout)):
            self.unsubscribe(token)
            error = self.last_error or "HDHomeRun source did not become ready."
            raise RuntimeError(error)
        if self.last_error and not self.is_running():
            self.unsubscribe(token)
            raise RuntimeError(self.last_error)
        return token, subscriber_queue

    def unsubscribe(self, token: object) -> None:
        with self.lock:
            self.subscribers.pop(token, None)
            self.last_activity_at = time.time()
            if not self.subscribers and self.is_running():
                self.idle_deadline = self.last_activity_at + self.idle_timeout_secs

    def _broadcast(self, chunk: bytes) -> None:
        with self.lock:
            # Keep a rolling MPEG-TS-aligned history for late joiners.
            self.prebuffer.append(chunk)
            targets = list(self.subscribers.values())
            self.last_activity_at = time.time()
        for target in targets:
            try:
                target.put_nowait(chunk)
            except queue.Full:
                # Live TV should remain live.  Drop the oldest buffered chunk
                # rather than allowing a slow client to stall the shared tuner.
                try:
                    target.get_nowait()
                except queue.Empty:
                    pass
                try:
                    target.put_nowait(chunk)
                except queue.Full:
                    pass

    def _should_stop_for_idle(self) -> bool:
        with self.lock:
            deadline = self.idle_deadline
            return bool(deadline is not None and not self.subscribers and time.time() >= deadline)

    def _run(self) -> None:
        self.manager._log(
            "info",
            f"Opening shared HDHomeRun source '{self.channel_name}' ({self.channel_key}).",
        )
        retry_delay = 0.5
        try:
            while not self.stop_event.is_set():
                if self._should_stop_for_idle():
                    break
                try:
                    response = requests.get(
                        self.stream_url,
                        stream=True,
                        timeout=(5, 20),
                        headers={"User-Agent": "RetroStation-MC/1.5 HDHomeRun Shared Source"},
                    )
                    response.raise_for_status()
                    with self.lock:
                        self.upstream = response
                        self.last_error = ""
                    self.ready_event.set()
                    retry_delay = 0.5
                    pending = b""
                    for chunk in response.iter_content(chunk_size=_CHUNK):
                        if self.stop_event.is_set() or self._should_stop_for_idle():
                            break
                        if not chunk:
                            continue
                        pending += chunk

                        # HTTP chunk boundaries are not guaranteed to align to
                        # 188-byte MPEG-TS packets.  Re-synchronize before
                        # feeding the shared relay so every subscriber sees
                        # complete transport packets.
                        while pending:
                            if pending[0] != 0x47:
                                sync = pending.find(b"\x47")
                                if sync < 0:
                                    pending = pending[-(_TS_PACKET_SIZE - 1):]
                                    break
                                pending = pending[sync:]
                            if len(pending) < _TS_PACKET_SIZE:
                                break
                            packet_count = min(len(pending) // _TS_PACKET_SIZE, _CHUNK // _TS_PACKET_SIZE)
                            size = packet_count * _TS_PACKET_SIZE
                            block = pending[:size]
                            pending = pending[size:]
                            if not block:
                                break
                            self.bytes_read += len(block)
                            self._broadcast(block)
                    response.close()
                    with self.lock:
                        if self.upstream is response:
                            self.upstream = None
                    if self.stop_event.is_set() or self._should_stop_for_idle():
                        break
                    # Unexpected EOF while still needed: reconnect the one
                    # physical source rather than making each consumer reconnect.
                    self.manager._log(
                        "warning",
                        f"Shared HDHomeRun source '{self.channel_name}' ended; reconnecting.",
                    )
                except requests.RequestException as exc:
                    with self.lock:
                        self.last_error = str(exc)
                    self.ready_event.set()
                    if self._should_stop_for_idle() or self.stop_event.is_set():
                        break
                    self.manager._log(
                        "warning",
                        f"Shared HDHomeRun source '{self.channel_name}' error: {exc}; reconnecting.",
                    )
                # If nobody is consuming, preserve the warm session only until
                # the configured grace deadline.  Otherwise retry quickly.
                end = time.time() + retry_delay
                while time.time() < end and not self.stop_event.is_set():
                    if self._should_stop_for_idle():
                        break
                    time.sleep(0.05)
                retry_delay = min(3.0, retry_delay * 2)
        finally:
            with self.lock:
                response = self.upstream
                self.upstream = None
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            self.ready_event.set()
            self.manager._session_stopped(self)
            self.manager._log(
                "info",
                f"Closed shared HDHomeRun source '{self.channel_name}' ({self.channel_key}).",
            )

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            response = self.upstream
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def status(self) -> dict:
        with self.lock:
            subscriber_count = len(self.subscribers)
            idle_deadline = self.idle_deadline
        return {
            "device_key": self.device_key,
            "channel_key": self.channel_key,
            "channel_name": self.channel_name,
            "stream_url": self.stream_url,
            "state": "running" if self.is_running() else ("failed" if self.last_error else "stopped"),
            "subscribers": subscriber_count,
            "started_at": self.started_at or None,
            "last_activity_at": self.last_activity_at or None,
            "idle_deadline": idle_deadline,
            "idle_timeout_secs": self.idle_timeout_secs,
            "bytes_read": self.bytes_read,
            "prebuffer_bytes": sum(len(item) for item in self.prebuffer),
            "last_error": self.last_error,
        }


class _RelayHandler(BaseHTTPRequestHandler):
    server_version = "RetroStationHDHRRelay/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        prefix = "/source/"
        if not parsed.path.startswith(prefix):
            self.send_error(404)
            return
        registry_key = unquote(parsed.path[len(prefix):])
        manager: HDHomeRunSharedSourceManager = self.server.manager  # type: ignore[attr-defined]
        try:
            token, subscriber = manager.subscribe_registry(registry_key)
        except KeyError:
            self.send_error(404)
            return
        except RuntimeError as exc:
            payload = (str(exc) + "\n").encode("utf-8", errors="replace")
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except OSError:
                pass
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                session = manager.get_registry(registry_key)
                if session is None:
                    break
                try:
                    chunk = subscriber.get(timeout=2.0)
                except queue.Empty:
                    if not session.is_running():
                        break
                    continue
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
        finally:
            manager.unsubscribe_registry(registry_key, token)


class HDHomeRunSharedSourceManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, _SourceSession] = {}
        self._logger: Callable[[str, str], None] | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None

    def set_logger(self, logger: Callable[[str, str], None] | None) -> None:
        self._logger = logger

    def _log(self, level: str, message: str) -> None:
        if self._logger is not None:
            try:
                self._logger(level, message)
            except Exception:
                pass

    def _ensure_server(self) -> ThreadingHTTPServer:
        with self._lock:
            if self._server is not None:
                return self._server
            server = ThreadingHTTPServer(("127.0.0.1", 0), _RelayHandler)
            server.daemon_threads = True
            server.manager = self  # type: ignore[attr-defined]
            thread = threading.Thread(target=server.serve_forever, name="hdhr-shared-relay", daemon=True)
            thread.start()
            self._server = server
            self._server_thread = thread
            self._log("info", f"HDHomeRun shared relay listening on 127.0.0.1:{server.server_port}.")
            return server

    def _registry_key(self, config: dict, channel_key: str) -> str:
        return f"{_device_key(config)}\x1f{channel_key}"

    def register(self, config: dict, channel: dict) -> tuple[str, str]:
        channel_key = str(channel.get("key") or "").strip()
        stream_url = str(channel.get("URL") or "").strip()
        if not channel_key or not stream_url.startswith(("http://", "https://")):
            raise ValueError("Invalid HDHomeRun channel source.")
        registry_key = self._registry_key(config, channel_key)
        name = str(channel.get("GuideName") or channel.get("GuideNumber") or channel_key).strip()
        with self._lock:
            existing = self._sessions.get(registry_key)
            if existing is not None and existing.stream_url != stream_url:
                existing.stop()
                self._sessions.pop(registry_key, None)
                existing = None
            if existing is None:
                existing = _SourceSession(
                    manager=self,
                    registry_key=registry_key,
                    device_key=_device_key(config),
                    channel_key=channel_key,
                    channel_name=name,
                    stream_url=stream_url,
                    tuner_count=_tuner_count(config),
                    idle_timeout_secs=_idle_timeout(config),
                )
                self._sessions[registry_key] = existing
            else:
                existing.channel_name = name
                existing.tuner_count = _tuner_count(config)
                existing.idle_timeout_secs = _idle_timeout(config)
        server = self._ensure_server()
        return registry_key, f"http://127.0.0.1:{server.server_port}/source/{quote(registry_key, safe='')}"

    def relay_url(self, config: dict, channel: dict) -> str:
        return self.register(config, channel)[1]

    def _capacity_available(self, candidate: _SourceSession) -> bool:
        if not candidate.tuner_count:
            return True
        with self._lock:
            active = sum(
                1 for session in self._sessions.values()
                if session is not candidate
                and session.device_key == candidate.device_key
                and session.is_running()
            )
        return active < candidate.tuner_count

    def subscribe_registry(self, registry_key: str) -> tuple[object, queue.Queue]:
        with self._lock:
            session = self._sessions.get(registry_key)
        if session is None:
            raise KeyError(registry_key)
        return session.subscribe()

    def unsubscribe_registry(self, registry_key: str, token: object) -> None:
        with self._lock:
            session = self._sessions.get(registry_key)
        if session is not None:
            session.unsubscribe(token)

    def get_registry(self, registry_key: str) -> _SourceSession | None:
        with self._lock:
            return self._sessions.get(registry_key)

    def _session_stopped(self, session: _SourceSession) -> None:
        # Keep the registration object so a future request reuses metadata and
        # starts a fresh physical session without rebuilding the registry.
        pass

    def stop_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.stop()

    def statuses(self) -> list[dict]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [session.status() for session in sessions]


shared_hdhomerun_sources = HDHomeRunSharedSourceManager()


def shared_hdhomerun_source_url(config: dict, channel: dict) -> str:
    """Return the localhost relay URL for a physical HDHomeRun channel."""
    return shared_hdhomerun_sources.relay_url(config, channel)
