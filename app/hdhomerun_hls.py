from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

HLS_SEGMENT_SECONDS = 6
HLS_LIST_SIZE = 10
HLS_READY_SEGMENTS = 3
HLS_KEYFRAME_INTERVAL_SECONDS = 2

_INTERLACED_FIELD_ORDERS = {"tt", "bb", "tb", "bt"}
_H264_CODECS = {"h264", "avc", "avc1"}
_MPEG2_CODECS = {"mpeg2video", "mpeg2"}


def _parse_rate(value: object) -> float:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return 0.0
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def probe_hdhomerun_video(stream_url: str, *, timeout: float = 8.0) -> dict:
    """Probe the first video stream so the HDHomeRun pipeline can be source-aware.

    The HDHomeRun lineup reports a coarse codec label but not the field order,
    dimensions, or actual frame rate required to choose a safe Ivy Bridge/i965
    pipeline.  A short ffprobe is therefore performed once when a session starts.
    """
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,profile,width,height,field_order,r_frame_rate,avg_frame_rate,pix_fmt,level",
        "-of", "json",
        stream_url,
    ]
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=max(1.0, float(timeout)),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffprobe failed").strip()
        raise RuntimeError(detail[-1200:])
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid ffprobe JSON: {exc}") from exc
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list) or not streams:
        raise RuntimeError("ffprobe did not report a video stream")
    stream = streams[0] if isinstance(streams[0], dict) else {}

    codec = str(stream.get("codec_name") or "").strip().lower()
    field_order = str(stream.get("field_order") or "unknown").strip().lower()
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate"))
    interlaced = field_order in _INTERLACED_FIELD_ORDERS
    return {
        "codec": codec,
        "profile": str(stream.get("profile") or "").strip(),
        "width": width,
        "height": height,
        "field_order": field_order,
        "interlaced": interlaced,
        "fps": fps,
        "r_frame_rate": str(stream.get("r_frame_rate") or ""),
        "avg_frame_rate": str(stream.get("avg_frame_rate") or ""),
        "pix_fmt": str(stream.get("pix_fmt") or "").strip(),
        "level": stream.get("level"),
    }


def _output_fps(source_profile: dict | None) -> float:
    """Return the effective output frame rate after source normalization.

    Interlaced OTA sources are bob-deinterlaced with YADIF send_field, which
    emits one progressive frame per field.  A 29.97i source therefore becomes
    approximately 59.94p and the GOP calculation must use that output cadence.
    """
    profile = source_profile or {}
    fps = float(profile.get("fps") or 0.0)
    if fps <= 0:
        fps = 30.0
    if bool(profile.get("interlaced")):
        fps *= 2.0
    return fps




def _output_rate_arg(source_profile: dict | None) -> str:
    """Return a stable CFR output rate for normalized interlaced sources."""
    profile = source_profile or {}
    raw = str(profile.get("avg_frame_rate") or profile.get("r_frame_rate") or "").strip()
    if bool(profile.get("interlaced")) and "/" in raw:
        try:
            numerator, denominator = raw.split("/", 1)
            n = int(numerator)
            d = int(denominator)
            if n > 0 and d > 0:
                return f"{n * 2}/{d}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    fps = _output_fps(profile)
    if abs(fps - 59.94005994) < 0.05:
        return "60000/1001"
    if abs(fps - 29.97002997) < 0.05:
        return "30000/1001"
    return f"{fps:.6f}".rstrip("0").rstrip(".")

def _gop_frames(source_profile: dict | None) -> int:
    return max(1, int(round(_output_fps(source_profile) * HLS_KEYFRAME_INTERVAL_SECONDS)))


def _software_video_filters(source_profile: dict | None) -> list[str]:
    """Return CPU-side normalization required before encode/upload."""
    profile = source_profile or {}
    filters: list[str] = []
    if bool(profile.get("interlaced")):
        # Ivy Bridge/i965 h264_vaapi fails with packed-header ENOSPC when fed
        # interlaced OTA frames directly.  Bob-deinterlace with YADIF send_field
        # so NTSC 29.97i becomes ~59.94p, preserving full field-rate motion
        # before upload to the encoder.
        filters.append("yadif=mode=send_field:parity=auto:deint=all")

    width = int(profile.get("width") or 0)
    height = int(profile.get("height") or 0)
    # Normalize non-720-wide SD MPEG-2 (for example 704x480) to the standard
    # raster that was validated successfully on the Ivy Bridge test host.
    if height == 480 and width and width != 720:
        filters.append("scale=720:480")
    elif height == 576 and width and width != 720:
        filters.append("scale=720:576")
    return filters


def build_hdhomerun_hls_command(
    stream_url: str,
    playlist_path: Path,
    segment_pattern: Path,
    *,
    use_vaapi: bool = False,
    vaapi_device: str | None = None,
    hardware_decode: bool = False,
    source_profile: dict | None = None,
    video_copy: bool = False,
) -> list[str]:
    """Build a source-aware HDHomeRun -> H.264/AAC MPEG-TS HLS pipeline.

    Policy validated on the RSMC Ivy Bridge/i965 test host:
      * Native H.264: copy video bitstream; transcode audio to AAC only.
      * Interlaced MPEG-2: software decode + YADIF send_field -> 59.94p -> h264_vaapi.
      * Progressive MPEG-2: VA-API decode+encode when possible, with software
        decode + VA-API encode as the hardware-encode fallback.
      * libx264 is the final fallback for MPEG-2 when VA-API cannot be used.
    """
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
    resolved_vaapi_device = vaapi_device or os.environ.get("RSMC_VAAPI_DEVICE", "/dev/dri/renderD128")

    if use_vaapi and not video_copy:
        command.extend(["-vaapi_device", resolved_vaapi_device])

    command.extend([
        "-fflags", "+genpts+discardcorrupt",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ])

    profile = source_profile or {}
    interlaced = bool(profile.get("interlaced"))

    # Hardware decode is used only for progressive material.  Interlaced OTA
    # MPEG-2 currently needs the proven CPU YADIF stage before VA-API encode.
    effective_hw_decode = bool(use_vaapi and hardware_decode and not interlaced and not video_copy)
    if effective_hw_decode:
        command.extend([
            "-hwaccel", "vaapi",
            "-hwaccel_device", resolved_vaapi_device,
            "-hwaccel_output_format", "vaapi",
        ])

    command.extend(["-i", stream_url, "-map", "0:v:0", "-map", "0:a:0?"])

    if video_copy:
        command.extend(["-c:v", "copy"])
    elif use_vaapi:
        if not effective_hw_decode:
            filters = _software_video_filters(profile)
            filters.extend(["format=nv12", "hwupload"])
            command.extend(["-vf", ",".join(filters)])
        gop = _gop_frames(profile)
        command.extend([
            "-c:v", "h264_vaapi",
            "-qp", "23",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECONDS})",
            "-sc_threshold", "0",
        ])
        if interlaced:
            command.extend(["-r", _output_rate_arg(profile), "-fps_mode", "cfr"])
    else:
        filters = _software_video_filters(profile)
        if filters:
            command.extend(["-vf", ",".join(filters)])
        gop = _gop_frames(profile)
        command.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECONDS})",
            "-sc_threshold", "0",
        ])
        if interlaced:
            command.extend(["-r", _output_rate_arg(profile), "-fps_mode", "cfr"])

    command.extend([
        "-c:a", "aac",
        "-b:a", "128k",
        "-ac", "2",
        "-ar", "48000",
    ])
    if not video_copy:
        command.extend(["-af", "aresample=async=1:first_pts=0"])

    command.extend([
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_SECONDS),
        "-hls_segment_type", "mpegts",
        "-hls_list_size", str(HLS_LIST_SIZE),
        "-segment_list_flags", "+live",
        "-hls_flags", "delete_segments+program_date_time+omit_endlist+discont_start+independent_segments",
        "-start_number", str(int(time.time()) // HLS_SEGMENT_SECONDS),
        "-hls_segment_filename", str(segment_pattern),
        str(playlist_path),
    ])
    return command


def build_hdhomerun_mpegts_command(
    stream_url: str,
    *,
    use_vaapi: bool = False,
    vaapi_device: str | None = None,
    hardware_decode: bool = False,
    source_profile: dict | None = None,
    video_copy: bool = False,
) -> list[str]:
    """Build a continuous MPEG-TS stdout pipeline using the same source-aware policy as HLS."""
    command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
    resolved_vaapi_device = vaapi_device or os.environ.get("RSMC_VAAPI_DEVICE", "/dev/dri/renderD128")

    if use_vaapi and not video_copy:
        command.extend(["-vaapi_device", resolved_vaapi_device])

    command.extend([
        "-fflags", "+genpts+discardcorrupt",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ])

    profile = source_profile or {}
    interlaced = bool(profile.get("interlaced"))
    effective_hw_decode = bool(use_vaapi and hardware_decode and not interlaced and not video_copy)
    if effective_hw_decode:
        command.extend([
            "-hwaccel", "vaapi",
            "-hwaccel_device", resolved_vaapi_device,
            "-hwaccel_output_format", "vaapi",
        ])

    command.extend(["-i", stream_url, "-map", "0:v:0", "-map", "0:a:0?"])

    if video_copy:
        command.extend(["-c:v", "copy"])
    elif use_vaapi:
        if not effective_hw_decode:
            filters = _software_video_filters(profile)
            filters.extend(["format=nv12", "hwupload"])
            command.extend(["-vf", ",".join(filters)])
        gop = _gop_frames(profile)
        command.extend([
            "-c:v", "h264_vaapi",
            "-qp", "23",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECONDS})",
            "-sc_threshold", "0",
        ])
        if interlaced:
            command.extend(["-r", _output_rate_arg(profile), "-fps_mode", "cfr"])
    else:
        filters = _software_video_filters(profile)
        if filters:
            command.extend(["-vf", ",".join(filters)])
        gop = _gop_frames(profile)
        command.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECONDS})",
            "-sc_threshold", "0",
        ])
        if interlaced:
            command.extend(["-r", _output_rate_arg(profile), "-fps_mode", "cfr"])

    command.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000"])
    if not video_copy:
        command.extend(["-af", "aresample=async=1:first_pts=0"])
    command.extend(["-f", "mpegts", "pipe:1"])
    return command


class HDHomeRunHLSSession:
    """One on-demand HDHomeRun station normalized to RSMC-style HLS."""

    def __init__(
        self,
        channel_key: str,
        channel_name: str,
        stream_url: str,
        output_dir: Path,
        *,
        idle_timeout_secs: int = 30,
        logger: Callable[[str, str], None] | None = None,
    ) -> None:
        self.channel_key = str(channel_key)
        self.channel_name = str(channel_name)
        self.stream_url = str(stream_url)
        self.output_dir = Path(output_dir)
        self.idle_timeout_secs = max(15, int(idle_timeout_secs or 0))
        self.logger = logger

        import hashlib
        digest = hashlib.sha1(self.channel_key.encode("utf-8")).hexdigest()[:16]
        self.file_prefix = f"hdhr_{digest}"
        self.playlist_path = self.output_dir / f"{self.file_prefix}.m3u8"
        self.segment_pattern = self.output_dir / f"{self.file_prefix}_%d.ts"

        self.proc: subprocess.Popen | None = None
        self.encoder = ""
        self.last_error = ""
        self.last_access_at = 0.0
        self.started_at = 0.0
        self.source_profile: dict | None = None
        self._lock = threading.RLock()
        self._idle_timer: threading.Timer | None = None
        self._stderr_lines: deque[str] = deque(maxlen=20)
        self._stderr_thread: threading.Thread | None = None

    def _log(self, level: str, message: str) -> None:
        if self.logger is not None:
            self.logger(level, message)

    def _cleanup_files(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.playlist_path.unlink(missing_ok=True)
        for path in self.output_dir.glob(f"{self.file_prefix}_*.ts"):
            path.unlink(missing_ok=True)

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, b""):
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self._stderr_lines.append(line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _ensure_source_profile(self) -> dict:
        if self.source_profile is None:
            self.source_profile = probe_hdhomerun_video(self.stream_url)
            p = self.source_profile
            scan = "interlaced" if p.get("interlaced") else "progressive"
            self._log(
                "info",
                f"Probed HDHomeRun source '{self.channel_name}': "
                f"codec={p.get('codec') or 'unknown'} {p.get('width')}x{p.get('height')} "
                f"{p.get('fps'):.3f}fps {scan} field_order={p.get('field_order') or 'unknown'}.",
            )
        return self.source_profile

    def _spawn(self, *, use_vaapi: bool, hardware_decode: bool = False, video_copy: bool = False) -> None:
        self._cleanup_files()
        profile = self._ensure_source_profile()
        command = build_hdhomerun_hls_command(
            self.stream_url,
            self.playlist_path,
            self.segment_pattern,
            use_vaapi=use_vaapi,
            hardware_decode=hardware_decode,
            source_profile=profile,
            video_copy=video_copy,
        )
        if video_copy:
            self.encoder = "H.264 video copy"
        elif use_vaapi and hardware_decode and not profile.get("interlaced"):
            self.encoder = "h264_vaapi (VA-API decode+encode)"
        elif use_vaapi and profile.get("interlaced"):
            self.encoder = "h264_vaapi (YADIF deinterlace + hardware encode)"
        elif use_vaapi:
            self.encoder = "h264_vaapi (software decode + hardware encode)"
        else:
            self.encoder = "libx264"
        self.last_error = ""
        self._stderr_lines.clear()
        self._log("info", f"Starting HDHomeRun HLS '{self.channel_name}' with {self.encoder}")
        child_env = os.environ.copy()
        if use_vaapi:
            configured_driver = str(os.environ.get("RSMC_VAAPI_DRIVER") or os.environ.get("LIBVA_DRIVER_NAME") or "").strip()
            if configured_driver:
                child_env["LIBVA_DRIVER_NAME"] = configured_driver

        self.proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=child_env,
        )
        self.started_at = time.time()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self.proc,),
            name=f"hdhr-hls-stderr-{self.file_prefix}",
            daemon=True,
        )
        self._stderr_thread.start()

    def _playlist_ready(self) -> bool:
        """Require a small completed HLS buffer before exposing the stream.

        A single completed segment leaves players very close to the live edge and
        can cause a short pause while the next segment is still being finalized.
        Keep three complete, non-empty segments available before reporting the
        session ready so clients start with roughly 18 seconds of safety margin.
        """
        try:
            text = self.playlist_path.read_text(encoding="utf-8")
        except OSError:
            return False
        if "#EXTM3U" not in text:
            return False
        target_match = re.search(r"^#EXT-X-TARGETDURATION:(\d+)\s*$", text, re.MULTILINE)
        if target_match is None or int(target_match.group(1)) <= 0:
            return False

        lines = text.splitlines()
        completed_segments = 0
        for index, line in enumerate(lines):
            if not line.startswith("#EXTINF:"):
                continue
            duration_text = line[len("#EXTINF:"):].split(",", 1)[0].strip()
            try:
                duration = float(duration_text)
            except ValueError:
                continue
            if duration <= 0.1:
                continue
            for media_line in lines[index + 1:]:
                media_line = media_line.strip()
                if not media_line or media_line.startswith("#"):
                    continue
                segment_name = media_line.rsplit("/", 1)[-1]
                segment_path = self.output_dir / segment_name
                try:
                    if segment_path.is_file() and segment_path.stat().st_size > 0:
                        completed_segments += 1
                except OSError:
                    pass
                break
            if completed_segments >= HLS_READY_SEGMENTS:
                return True
        return False

    def _wait_until_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            proc = self.proc
            if proc is None:
                break
            if self._playlist_ready():
                return True
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        return self._playlist_ready()

    def start(self, *, prefer_vaapi: bool = False, startup_timeout: float = 22.0) -> bool:
        with self._lock:
            if self.proc is not None and self.proc.poll() is None and self._playlist_ready():
                self.touch()
                return True
            self.stop(remove_files=False)

            try:
                profile = self._ensure_source_profile()
            except Exception as exc:
                self.last_error = f"Unable to probe HDHomeRun source: {exc}"
                self._log("warning", self.last_error)
                return False

            codec = str(profile.get("codec") or "").lower()
            interlaced = bool(profile.get("interlaced"))

            if codec in _H264_CODECS:
                # Already-compatible H.264 is best left untouched.  Re-encoding
                # would waste GPU/CPU, add latency, and reduce quality.
                attempts = [(False, False, True)]
            elif prefer_vaapi:
                if interlaced:
                    # Proven Ivy Bridge path: CPU MPEG-2 decode + YADIF to
                    # progressive frames, then VA-API H.264 encode.
                    attempts = [(True, False, False), (False, False, False)]
                else:
                    # Progressive MPEG-2 can remain on the GPU end-to-end.
                    attempts = [
                        (True, True, False),
                        (True, False, False),
                        (False, False, False),
                    ]
            else:
                attempts = [(False, False, False)]

            for use_vaapi, hardware_decode, video_copy in attempts:
                try:
                    self._spawn(
                        use_vaapi=use_vaapi,
                        hardware_decode=hardware_decode,
                        video_copy=video_copy,
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    self.last_error = f"Unable to start FFmpeg: {exc}"
                    self._log("warning", self.last_error)
                    continue
                if self._wait_until_ready(startup_timeout):
                    self.touch()
                    return True

                proc = self.proc
                return_code = proc.poll() if proc is not None else None
                stderr_tail = " | ".join(self._stderr_lines)
                self.last_error = (
                    f"HDHomeRun HLS did not become ready using {self.encoder}"
                    + (f" (ffmpeg rc={return_code})" if return_code is not None else "")
                    + (f": {stderr_tail}" if stderr_tail else "")
                )
                self._log("warning", self.last_error)
                self.stop(remove_files=True)
            return False

    def touch(self) -> None:
        with self._lock:
            self.last_access_at = time.time()
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            timer = threading.Timer(self.idle_timeout_secs, self._idle_expired)
            timer.daemon = True
            self._idle_timer = timer
            timer.start()

    def _idle_expired(self) -> None:
        with self._lock:
            elapsed = time.time() - self.last_access_at
            if elapsed + 0.25 < self.idle_timeout_secs:
                self.touch()
                return
        self.stop(remove_files=True)

    def stop(self, *, remove_files: bool = True) -> None:
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            proc = self.proc
            self.proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if remove_files:
            self._cleanup_files()

    def is_running(self) -> bool:
        proc = self.proc
        return proc is not None and proc.poll() is None

    def status(self) -> dict:
        return {
            "channel_key": self.channel_key,
            "channel_name": self.channel_name,
            "state": "running" if self.is_running() else ("failed" if self.last_error else "stopped"),
            "encoder": self.encoder,
            "source_profile": dict(self.source_profile or {}),
            "started_at": self.started_at or None,
            "last_access_at": self.last_access_at or None,
            "idle_timeout_secs": self.idle_timeout_secs,
            "last_error": self.last_error,
        }
