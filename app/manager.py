from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .config_store import ConfigStore
from .guide_state import STATE_PATH, build_state
from .logging_utils import AppLogger
from .m3u_parser import parse_m3u
from .xmltv_parser import parse_xmltv

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
MUSIC_DIR = DATA_DIR / "music"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

STANDBY_SEGMENT = OUTPUT_DIR / "standby.ts"

# Path to the bundled sample XMLTV file.  When this file is the configured
# source, programme data is generated dynamically so the guide always shows
# current content regardless of when the application was deployed.
SAMPLE_XMLTV_PATH = BASE_DIR / "sample_data" / "xmltv.xml"

# Rotating show titles for each bundled sample channel.
# Each list is cycled in order so every 2-hour slot has a distinct label.
_SAMPLE_SCHEDULE: dict[str, list[tuple[str, str]]] = {
    "channel.2": [
        ("Evening Headlines", "Local and regional news."),
        ("Metro Forecast", "Weather and traffic."),
        ("Prime Interview", "Studio discussion."),
        ("Late Night with WRETRO", "Talk and entertainment."),
    ],
    "channel.4": [
        ("National Report", "Top stories of the hour."),
        ("World Desk", "International coverage."),
        ("Morning Briefing", "Early headlines."),
        ("Midday Update", "Afternoon news round-up."),
    ],
    "channel.5": [
        ("Sunday Movie", "Feature presentation."),
        ("Late Feature", "Back-to-back movie night."),
        ("Classic Cinema", "Golden era film."),
        ("Midnight Screening", "Late-night double bill."),
    ],
    "channel.7": [
        ("Pre-Game Live", "Sports desk and highlights."),
        ("Sunday Night Baseball", "Live game coverage."),
        ("Post-Game Show", "Analysis and interviews."),
        ("Sports Highlight Reel", "Best plays of the week."),
    ],
    "channel.9": [
        ("Cartoon Hour", "Animated fun block."),
        ("Science Kids", "Learning adventure."),
        ("Story Time", "Tales for young viewers."),
        ("Nature Junior", "Wildlife for kids."),
    ],
    "channel.11": [
        ("Ancient Worlds", "Documentary series."),
        ("On This Day", "Historic moments."),
        ("Lost Cities", "Archaeological discoveries."),
        ("Timeline", "Events that shaped history."),
    ],
    "channel.13": [
        ("Retro Hits", "Music video block."),
        ("Live Sessions", "Studio performances."),
        ("Chart Rewind", "Top tracks of the decade."),
        ("Acoustic Set", "Unplugged performances."),
    ],
    "channel.15": [
        ("Standup Showcase", "Comedy block."),
        ("Classic Sitcoms", "Back-to-back episodes."),
        ("Sketch Night", "Comedy sketch compilation."),
        ("Improv Hour", "Live comedy special."),
    ],
    "channel.18": [
        ("Earth From Orbit", "Space imagery feed."),
        ("Deep Space Window", "Curated NASA imagery."),
        ("Solar System Tour", "Planetary close-ups."),
        ("Night Sky Live", "Real-time observatory feed."),
    ],
}


def _generate_sample_programmes() -> dict[str, list[dict]]:
    """Return dynamically-dated programme data for the bundled sample channels.

    Generates 2-hour blocks starting from the current 2-hour UTC boundary and
    covering 7 days forward.  Each channel's show titles rotate through
    :data:`_SAMPLE_SCHEDULE` so the rendered guide always displays named
    content rather than the 'No guide data' fallback.

    Note: these blocks feed the *rendered video guide* (the retro channel-list
    video).  The *IPTV client EPG* entry for the virtual guide channel uses
    separate 4-hour blocks produced by :func:`_build_xmltv_content` in
    ``app.py``.
    """
    now = datetime.now(timezone.utc)
    slot_start = now.replace(hour=(now.hour // 2) * 2, minute=0, second=0, microsecond=0)
    total_slots = 7 * 12  # 7 days × 12 two-hour slots/day

    programmes: dict[str, list[dict]] = {}
    for channel_id, schedule in _SAMPLE_SCHEDULE.items():
        channel_progs: list[dict] = []
        t = slot_start
        for i in range(total_slots):
            title, desc = schedule[i % len(schedule)]
            t_end = t + timedelta(hours=2)
            channel_progs.append(
                {
                    "title": title,
                    "desc": desc,
                    "start": t.isoformat(),
                    "stop": t_end.isoformat(),
                }
            )
            t = t_end
        programmes[channel_id] = channel_progs
    return programmes


# Duration (seconds) of the encoded standby.ts segment.  A longer clip means
# fewer EXT-X-DISCONTINUITY events in the standby HLS playlist, so players
# don't stall or appear to freeze while the guide pipeline is warming up.
# 30 seconds is long enough to cover most renderer startup times without the
# standby being interrupted by a discontinuity.
STANDBY_DURATION_SECS = 30

# Keyframe interval used by the HLS segmenter (seconds).  Matching ErsatzTV's
# KeyframeIntervalSeconds constant: a keyframe every 2 s ensures every 4-second
# segment boundary always lands on an iframe, which prevents player stalls and
# avoids the "grey frame" artifact seen on some IPTV clients when a segment
# starts on a non-keyframe.
HLS_KEYFRAME_INTERVAL_SECS = 2
HLS_TELEMETRY_WARN_INTERVAL_MULTIPLIER = 1.5
HLS_TELEMETRY_WARN_DURATION_VARIANCE_SECS = 0.15

# PID files let us reattach to the pipeline after a Flask restart without
# killing the already-running renderer and ffmpeg processes.
RENDERER_PID_FILE = DATA_DIR / "renderer.pid"
FFMPEG_PID_FILE = DATA_DIR / "ffmpeg.pid"


# ---------------------------------------------------------------------------
# Low-level PID helpers
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """Return True if a process with *pid* is currently running (not a zombie).

    On Linux, os.kill(pid, 0) succeeds for zombie processes because their
    process-table entry still exists.  A zombie cannot do useful work, so we
    treat it as dead by additionally checking /proc/<pid>/status.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it – treat as alive.
        return True
    # Secondary zombie check (Linux only; harmless no-op on other platforms).
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("State:"):
                # State line format: "State:\tZ (zombie)"
                return not line.split(maxsplit=2)[1].startswith("Z")
    except OSError:
        pass
    return True


def _pid_matches(pid: int, fragment: str) -> bool:
    """Return True if /proc/<pid>/cmdline contains *fragment*.

    Guarded against PID reuse: a recycled PID whose command line does not
    match will not be mistaken for our process.  Works on Linux only; on
    other platforms it always returns True so the caller falls back to a
    regular restart.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        return fragment.encode() in cmdline
    except OSError:
        return True  # not Linux or unreadable – give the caller the benefit of the doubt


def _load_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _save_pid(path: Path, pid: int) -> None:
    try:
        path.write_text(str(pid))
    except OSError:
        pass


def _terminate_pid(
    pid: int,
    popen: Optional[subprocess.Popen] = None,
    logger=None,
    label: str = "process",
) -> None:
    """Send SIGTERM to *pid*, wait up to 5 s, then SIGKILL if still alive.

    *popen* should be supplied when we are the parent of the process so
    that the zombie can be reaped via waitpid().  For reattached processes
    (where we are not the parent) pass None; init will reap them.
    """
    # If the process has already exited (possibly a zombie waiting to be
    # reaped), skip signalling and just call wait() to clean up the entry.
    if popen is not None and popen.poll() is not None:
        try:
            popen.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        # Also check via poll() so we don't spin for 5 s on a zombie.
        if popen is not None and popen.poll() is not None:
            break
        time.sleep(0.05)

    if _pid_alive(pid) and (popen is None or popen.poll() is None):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        else:
            if logger:
                logger.warning(
                    "pipeline",
                    f"{label} (PID {pid}) did not exit after SIGTERM; sent SIGKILL",
                )

    # Reap the zombie if we are its parent.
    if popen is not None:
        try:
            popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if logger:
                logger.warning(
                    "pipeline",
                    f"{label} (PID {pid}) did not exit after SIGKILL",
                )


# ---------------------------------------------------------------------------
# Standby frame helpers
# ---------------------------------------------------------------------------

def _load_font_for_standby(size: int):
    """Load a TrueType font for the standby card, falling back to PIL default."""
    from PIL import ImageFont
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _build_standby_image(width: int, height: int, title: str) -> "Image.Image":
    """Return a PIL Image with SMPTE-style colour bars and a 'Please Stand By' overlay."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    # SMPTE 75 % colour bars across the upper 75 % of the frame
    bars = [
        (191, 191, 191),  # 75 % white
        (191, 191, 0),    # yellow
        (0, 191, 191),    # cyan
        (0, 191, 0),      # green
        (191, 0, 191),    # magenta
        (191, 0, 0),      # red
        (0, 0, 191),      # blue
    ]
    bar_h = int(height * 0.75)
    bar_w = width // len(bars)
    for i, color in enumerate(bars):
        x0 = i * bar_w
        x1 = x0 + bar_w if i < len(bars) - 1 else width
        draw.rectangle([x0, 0, x1 - 1, bar_h - 1], fill=color)

    # Dark strip below the bars for the text message
    draw.rectangle([0, bar_h, width - 1, height - 1], fill=(15, 15, 15))

    sub_text = "Please Stand By"
    font_title = _load_font_for_standby(max(24, height // 18))
    font_sub = _load_font_for_standby(max(18, height // 24))

    def text_size(text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    tw, th = text_size(title, font_title)
    sw, sh = text_size(sub_text, font_sub)
    gap = max(6, height // 60)
    block_h = th + gap + sh
    y = bar_h + max(4, (height - bar_h - block_h) // 2)

    # Title line – white with drop shadow
    x = max(0, (width - tw) // 2)
    draw.text((x + 2, y + 2), title, font=font_title, fill=(0, 0, 0))
    draw.text((x, y), title, font=font_title, fill=(255, 255, 255))

    # Subtitle line – yellow with drop shadow
    y += th + gap
    x2 = max(0, (width - sw) // 2)
    draw.text((x2 + 2, y + 2), sub_text, font=font_sub, fill=(0, 0, 0))
    draw.text((x2, y), sub_text, font=font_sub, fill=(255, 220, 50))

    return img


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

def _build_audio_ffmpeg_args(config: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (input_args, codec_args, map_args) for the audio portion of the
    ffmpeg pipeline based on the music configuration in *config*.

    Three cases:
    - No music (mode == "none" or no valid files): silence via anullsrc.
    - Music with loop: ``-stream_loop -1 -i <source>`` as input 1.
    - Music without loop: anullsrc as input 1, music as input 2, mixed via
      ``amix`` (``normalize=0``) so silence fills after the music ends.

    Returns three separate lists because they slot into different positions
    inside the full ffmpeg command:
    - *input_args*  – placed after the video input (``-i -``).
    - *codec_args*  – audio encoder settings (``-c:a``, ``-b:a``).
    - *map_args*    – ``-map`` / ``-filter_complex`` selectors.
    """
    silence_input = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    silence_codec = ["-c:a", "aac", "-b:a", "32k"]
    silence_map = ["-map", "0:v", "-map", "1:a"]

    music_mode = config.get("music_mode", "none")
    music_loop = bool(config.get("music_loop", False))

    if music_mode == "single":
        filename = config.get("music_single_file", "")
        if filename:
            file_path = MUSIC_DIR / Path(filename).name
            if file_path.exists():
                if music_loop:
                    return (
                        ["-stream_loop", "-1", "-i", str(file_path)],
                        ["-c:a", "aac", "-b:a", "128k"],
                        ["-map", "0:v", "-map", "1:a"],
                    )
                else:
                    # Mix finite music file with infinite silence so encoding
                    # continues seamlessly after the track ends.
                    # normalize=0: disable amix's default 1/N volume scaling so
                    # the music plays at its original level (mixing in silence does
                    # not reduce the signal; when the music ends only the silent
                    # anullsrc contributes, producing clean silence).
                    return (
                        silence_input + ["-i", str(file_path)],
                        ["-c:a", "aac", "-b:a", "128k"],
                        [
                            "-filter_complex",
                            "[2:a][1:a]amix=inputs=2:duration=longest:normalize=0[outa]",
                            "-map", "0:v", "-map", "[outa]",
                        ],
                    )

    elif music_mode == "playlist":
        playlist_files = config.get("music_playlist_files", [])
        valid_files = [
            MUSIC_DIR / Path(f).name
            for f in playlist_files
            if (MUSIC_DIR / Path(f).name).exists()
        ]
        if valid_files:
            concat_file = DATA_DIR / "music_playlist.txt"
            try:
                # Escape single-quotes in paths (ffmpeg concat demuxer uses
                # single-quoted file entries; a literal ' must become '\'').
                def _esc(p: Path) -> str:
                    return str(p).replace("'", "'\\''")

                lines = "".join(f"file '{_esc(fp)}'\n" for fp in valid_files)
                concat_file.write_text(lines, encoding="utf-8")
            except OSError:
                return silence_input, silence_codec, silence_map

            concat_input = ["-f", "concat", "-safe", "0", "-i", str(concat_file)]
            if music_loop:
                return (
                    ["-stream_loop", "-1"] + concat_input,
                    ["-c:a", "aac", "-b:a", "128k"],
                    ["-map", "0:v", "-map", "1:a"],
                )
            else:
                # See normalize=0 comment above for the same pattern.
                return (
                    silence_input + concat_input,
                    ["-c:a", "aac", "-b:a", "128k"],
                    [
                        "-filter_complex",
                        "[2:a][1:a]amix=inputs=2:duration=longest:normalize=0[outa]",
                        "-map", "0:v", "-map", "[outa]",
                    ],
                )

    # Default: silence
    return silence_input, silence_codec, silence_map


def _start_stderr_reader(
    popen: subprocess.Popen,
    label: str,
    logger,
) -> threading.Thread:
    """Drain *popen*'s stderr pipe in a background thread, logging each line.

    This serves two purposes:
    1. Prevents the subprocess from blocking when its stderr pipe buffer fills.
    2. Surfaces crash messages (ffmpeg codec errors, Python tracebacks, etc.)
       in the app event log and on stdout for easy troubleshooting.
    """
    def _reader():
        try:
            for raw_line in popen.stderr:
                line = raw_line.rstrip() if isinstance(raw_line, str) else raw_line.rstrip().decode("utf-8", errors="replace")
                if line:
                    if line.startswith("telemetry:"):
                        logger.info(f"{label}.telemetry", line.split(":", 1)[1])
                    elif line.startswith("telemetry-warning:"):
                        logger.warning(f"{label}.telemetry", line.split(":", 1)[1])
                    else:
                        logger.warning(label, line)
        except Exception:
            pass

    t = threading.Thread(target=_reader, daemon=True, name=f"stderr-{label}")
    t.start()
    return t


class GuideManager:
    def __init__(self, store: ConfigStore):
        self.store = store
        self.logger = AppLogger(store)
        # PIDs of the pipeline processes (set on spawn OR reattach).
        self._renderer_pid: Optional[int] = None
        self._ffmpeg_pid: Optional[int] = None
        # Popen objects – only set for processes WE spawned so we can waitpid.
        self._renderer_popen: Optional[subprocess.Popen] = None
        self._ffmpeg_popen: Optional[subprocess.Popen] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.last_refresh_status = "never"
        # Wall-clock time (time.time()) when start_pipeline() was last called.
        # None means the pipeline was reattached rather than freshly started,
        # in which case the buffer is assumed already populated.
        self._pipeline_started_at: Optional[float] = None
        # True once the admin has explicitly started the pipeline (or we
        # reattached to a surviving one).  While False, ensure_pipeline_running
        # does nothing so the standby playlist is shown until the admin clicks
        # "Save and Start".
        self._pipeline_active: bool = False
        # Monotonically increasing counter that increments every time the
        # guide transitions between standby and live (in either direction).
        # Exposed via status() so frontends and master.m3u8 can carry it as a
        # cache-busting query parameter and detect transitions to reload.
        self._stream_version: int = 0
        self._last_was_buffered: bool = False
        self._telemetry_debug = os.getenv("RETRO_TELEMETRY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self._hls_telemetry_thread: Optional[threading.Thread] = None
        self._hls_telemetry_stop = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop_event.clear()
        if self._worker_thread and self._worker_thread.is_alive():
            return

        # Attempt to reattach to pipeline processes that survived a prior
        # Flask restart rather than blindly killing and respawning them.
        # This is the key mechanism that keeps the HLS stream alive across
        # Flask restarts: if the renderer and ffmpeg are still running,
        # we just record their PIDs and skip the kill/spawn cycle entirely.
        renderer_pid = _load_pid(RENDERER_PID_FILE)
        ffmpeg_pid = _load_pid(FFMPEG_PID_FILE)
        if (
            renderer_pid
            and _pid_alive(renderer_pid)
            and _pid_matches(renderer_pid, "renderer.py")
            and ffmpeg_pid
            and _pid_alive(ffmpeg_pid)
            and _pid_matches(ffmpeg_pid, "ffmpeg")
        ):
            self._renderer_pid = renderer_pid
            self._ffmpeg_pid = ffmpeg_pid
            self._pipeline_active = True
            # _renderer_popen / _ffmpeg_popen stay None because we are not
            # the parent of these processes; init owns them now.
            self.logger.info(
                "system",
                f"Reattached to existing pipeline "
                f"(renderer PID {renderer_pid}, ffmpeg PID {ffmpeg_pid})",
            )
        else:
            # No surviving pipeline – generate the standby segment and wait
            # for the admin to click "Save and Start" before starting the
            # guide pipeline.
            self.refresh_state()
            self._generate_standby_segment()
            self.logger.info(
                "system",
                "No existing pipeline found; standby playlist active — "
                "click 'Save and Start' to begin the guide.",
            )

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        self.logger.info("system", "Guide manager started")

    def stop(self) -> None:
        """Stop the background worker thread.

        Pipeline processes (renderer, ffmpeg) are intentionally left running
        so that the HLS stream survives a Flask restart.  Call stop_pipeline()
        explicitly if you need to terminate them.
        """
        self._stop_event.set()
        self.logger.info("system", "Guide manager stopped")

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.refresh_state()
                self.ensure_pipeline_running()
            except Exception as exc:
                try:
                    cfg = self.store.get_config()
                    playlist_source = cfg.get("playlist_source", "")
                    xmltv_source = cfg.get("xmltv_source", "")
                except Exception:
                    playlist_source = ""
                    xmltv_source = ""
                self.last_refresh_status = f"error: {exc}"
                self.logger.error(
                    "worker",
                    f"Refresh failed ({exc.__class__.__name__}): {exc}; "
                    f"playlist_source={playlist_source!r}; xmltv_source={xmltv_source!r}",
                )
                self.logger.error("worker.traceback", traceback.format_exc().strip())
            self._stop_event.wait(30)

    # ------------------------------------------------------------------
    # State / pipeline management
    # ------------------------------------------------------------------

    def refresh_state(self) -> None:
        config = self.store.get_config()
        playlist_source = config.get("playlist_source", "")
        xmltv_source = config.get("xmltv_source", "")
        try:
            channels = parse_m3u(playlist_source)
        except Exception as exc:
            self.logger.error(
                "refresh",
                f"Playlist load failed ({exc.__class__.__name__}): {exc}; "
                f"playlist_source={playlist_source!r}",
            )
            raise
        # When the configured XMLTV source is the bundled sample file, generate
        # programme data dynamically so the guide always shows current content
        # even after the static file's hardcoded dates have passed.
        try:
            is_sample = Path(xmltv_source).resolve() == SAMPLE_XMLTV_PATH.resolve()
        except (ValueError, OSError):
            is_sample = False
        if is_sample:
            programmes = _generate_sample_programmes()
        else:
            try:
                programmes = parse_xmltv(xmltv_source)
            except Exception as exc:
                self.logger.error(
                    "refresh",
                    f"XMLTV load failed ({exc.__class__.__name__}): {exc}; "
                    f"xmltv_source={xmltv_source!r}",
                )
                raise
        build_state(config, channels, programmes)
        self.last_refresh_status = "ok"
        self.logger.info("refresh", f"State rebuilt with {len(channels)} channel(s)")

    def _start_hls_telemetry_monitor(self, segment_seconds: float) -> None:
        if not self._telemetry_debug:
            return
        self._stop_hls_telemetry_monitor()
        self._hls_telemetry_stop.clear()
        target = max(0.5, float(segment_seconds))
        self.logger.info(
            "hls.telemetry",
            json.dumps(
                {
                    "type": "hls_telemetry_start",
                    "segment_target_secs": target,
                    "warn_interval_secs": round(target * HLS_TELEMETRY_WARN_INTERVAL_MULTIPLIER, 3),
                    "warn_duration_variance_secs": HLS_TELEMETRY_WARN_DURATION_VARIANCE_SECS,
                    "warn_playlist_cadence_secs": round(target * HLS_TELEMETRY_WARN_INTERVAL_MULTIPLIER, 3),
                },
                sort_keys=True,
            ),
        )

        def _monitor() -> None:
            playlist = OUTPUT_DIR / "guide.m3u8"
            last_playlist_mtime: float | None = None
            last_playlist_update_mono: float | None = None
            last_segment_name: str | None = None
            last_segment_update_mono: float | None = None
            segment_interval_samples: list[float] = []
            playlist_cadence_samples: list[float] = []
            segment_variance_samples: list[float] = []
            next_emit = time.monotonic() + 10.0
            while not self._hls_telemetry_stop.wait(1.0):
                now_mono = time.monotonic()
                try:
                    mtime = playlist.stat().st_mtime
                except OSError:
                    if now_mono >= next_emit:
                        next_emit = now_mono + 10.0
                    continue
                if last_playlist_mtime is None or mtime > last_playlist_mtime:
                    if last_playlist_update_mono is not None:
                        playlist_cadence_samples.append(now_mono - last_playlist_update_mono)
                    last_playlist_update_mono = now_mono
                    last_playlist_mtime = mtime
                    try:
                        lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
                    except OSError:
                        lines = []
                    durations: list[float] = []
                    segment_names: list[str] = []
                    pending_duration: float | None = None
                    for raw in lines:
                        line = raw.strip()
                        if line.startswith("#EXTINF:"):
                            try:
                                pending_duration = float(line.split(":", 1)[1].split(",", 1)[0].strip())
                            except ValueError:
                                pending_duration = None
                        elif line and not line.startswith("#"):
                            segment_names.append(line)
                            if pending_duration is not None:
                                durations.append(pending_duration)
                                pending_duration = None
                    if durations:
                        segment_variance_samples.append(max(durations) - min(durations))
                    if segment_names:
                        newest = segment_names[-1]
                        if last_segment_name is None:
                            last_segment_name = newest
                            last_segment_update_mono = now_mono
                        elif newest != last_segment_name:
                            if last_segment_update_mono is not None:
                                segment_interval_samples.append(now_mono - last_segment_update_mono)
                            last_segment_name = newest
                            last_segment_update_mono = now_mono

                if now_mono < next_emit:
                    continue
                payload = {
                    "type": "hls_telemetry",
                    "window_secs": 10.0,
                    "segment_target_secs": round(target, 3),
                    "segment_interval_secs_avg": round(sum(segment_interval_samples) / max(1, len(segment_interval_samples)), 3),
                    "segment_interval_secs_max": round(max(segment_interval_samples) if segment_interval_samples else 0.0, 3),
                    "segment_duration_variance_secs_avg": round(sum(segment_variance_samples) / max(1, len(segment_variance_samples)), 3),
                    "segment_duration_variance_secs_max": round(max(segment_variance_samples) if segment_variance_samples else 0.0, 3),
                    "playlist_update_cadence_secs_avg": round(sum(playlist_cadence_samples) / max(1, len(playlist_cadence_samples)), 3),
                    "playlist_update_cadence_secs_max": round(max(playlist_cadence_samples) if playlist_cadence_samples else 0.0, 3),
                }
                warnings: list[str] = []
                if payload["segment_interval_secs_max"] > (target * HLS_TELEMETRY_WARN_INTERVAL_MULTIPLIER):
                    warnings.append("segment_generation_slow")
                if payload["segment_duration_variance_secs_max"] > HLS_TELEMETRY_WARN_DURATION_VARIANCE_SECS:
                    warnings.append("segment_duration_variance_high")
                if payload["playlist_update_cadence_secs_max"] > (target * HLS_TELEMETRY_WARN_INTERVAL_MULTIPLIER):
                    warnings.append("playlist_update_cadence_slow")
                if warnings:
                    payload["warnings"] = warnings
                    self.logger.warning("hls.telemetry", json.dumps(payload, sort_keys=True))
                else:
                    self.logger.info("hls.telemetry", json.dumps(payload, sort_keys=True))
                segment_interval_samples.clear()
                playlist_cadence_samples.clear()
                segment_variance_samples.clear()
                next_emit = now_mono + 10.0

        self._hls_telemetry_thread = threading.Thread(target=_monitor, daemon=True, name="hls-telemetry")
        self._hls_telemetry_thread.start()

    def _stop_hls_telemetry_monitor(self) -> None:
        self._hls_telemetry_stop.set()
        thread = self._hls_telemetry_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._hls_telemetry_thread = None

    def _clean_output_dir(self) -> None:
        """Remove stale .ts segments and the HLS playlist from a prior run.

        Both the segment files and ``guide.m3u8`` are removed together so that
        clients never receive a playlist that references segments which no longer
        exist.  A stale playlist pointing at deleted segments causes HLS clients
        to fail with repeated 404 errors on the segment URLs and often stall
        indefinitely.

        ``standby.ts`` is intentionally preserved across restarts so that
        the standby playlist (served while guide.m3u8 is not yet ready) is
        immediately available without waiting for a new encode.

        Epoch-based ``-start_number`` (set in ``start_pipeline``) ensures that
        ``EXT-X-MEDIA-SEQUENCE`` always advances across restarts, so there is no
        risk of the new playlist being mistaken for stale content.
        """
        for path in OUTPUT_DIR.iterdir():
            if path == STANDBY_SEGMENT:
                continue
            if path.suffix in (".ts", ".m3u8"):
                try:
                    path.unlink()
                except OSError as exc:
                    self.logger.warning("pipeline", f"Could not remove stale output file {path}: {exc}")

    def _generate_standby_segment(self, title: str = "Guide is Loading...") -> None:
        """Encode a looping 'Please Stand By' segment into ``standby.ts``.

        Uses PIL to draw SMPTE-style colour bars with the given *title* message
        overlaid, then asks FFmpeg to encode it as a single MPEG-TS segment.
        Errors are caught and logged so a missing standby segment is never fatal.
        Called outside ``_lock`` because FFmpeg can take a second or two.
        """
        import tempfile

        config = self.store.get_config()
        resolution = config.get("resolution", "1280x720")
        fps = int(config.get("fps", 15))
        gop_size = fps * int(config.get("segment_seconds", 6))

        try:
            width, height = [int(x) for x in resolution.lower().split("x", 1)]
        except ValueError:
            width, height = 1280, 720

        try:
            img = _build_standby_image(width, height, title)
        except Exception as exc:
            self.logger.warning("pipeline", f"Could not build standby image: {exc}")
            return

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            img.save(tmp_path)

            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-loop", "1",
                "-framerate", str(fps),
                "-i", tmp_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p",
                "-g", str(gop_size),
                # Keep keyframes tightly aligned so standby.ts starts on a clean
                # iframe when looped by the standby playlist.
                "-keyint_min", str(gop_size),
                "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "32k",
                "-map", "0:v", "-map", "1:a",
                "-t", str(STANDBY_DURATION_SECS),
                str(STANDBY_SEGMENT),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                self.logger.warning("pipeline", f"Standby segment encode failed (exit {result.returncode}): {stderr}")
            else:
                self.logger.info("pipeline", f"Standby segment written to {STANDBY_SEGMENT}")
        except Exception as exc:
            self.logger.warning("pipeline", f"Could not generate standby segment: {exc}")
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def start_pipeline(self, message: str = "Guide is Loading...") -> None:
        # Mark the pipeline as intentionally active so the background worker
        # will restart it automatically if it ever crashes.
        self._pipeline_active = True
        # Record the start time *before* generating standby so the buffer gate
        # measures from the very beginning of the startup sequence.
        self._pipeline_started_at = time.time()

        # Generate (or refresh) the standby segment before tearing down the
        # running pipeline so that standby.ts is ready the moment guide.m3u8
        # disappears.  Done outside the lock; FFmpeg can take a second or two.
        self._generate_standby_segment(message)

        with self._lock:
            self._stop_pipeline_locked()
            self._clean_output_dir()

            config = self.store.get_config()
            resolution = config.get("resolution", "1280x720")
            fps = str(config.get("fps", 15))
            segment_seconds = str(config.get("segment_seconds", 6))
            playlist_path = OUTPUT_DIR / "guide.m3u8"

            # Use an epoch-time-based segment start number so that
            # EXT-X-MEDIA-SEQUENCE in guide.m3u8 always increases across
            # pipeline restarts.  Without this, every restart resets the
            # sequence to 0; HLS clients that were at a higher sequence
            # conclude "no new segments" and stall until their buffer empties.
            start_number = str(int(time.time()) // max(1, int(segment_seconds)))

            renderer_cmd = [
                sys.executable,
                str(BASE_DIR / "app" / "renderer.py"),
                "--state", str(STATE_PATH),
                "--fps", fps,
                "--resolution", resolution,
            ]
            if self._telemetry_debug:
                renderer_cmd.append("--telemetry")

            audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(config)
            # GOP size in frames. A 2-second keyframe interval keeps each 6-second
            # segment populated with multiple IDR points so segment boundaries can
            # start cleanly and avoid "grey frame" stalls in stricter IPTV clients.
            gop_frames = int(fps) * HLS_KEYFRAME_INTERVAL_SECS
            ffmpeg_cmd = [
                "ffmpeg",
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-s", resolution,
                "-r", fps,
                "-i", "-",
                *audio_input_args,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                # GOP / keyframe strategy mirrors ErsatzTV's OutputFormatHls:
                # -g / -keyint_min pin the GOP to exactly KeyframeIntervalSeconds
                # so ffmpeg never stretches it on scene-change detection.
                # -force_key_frames guarantees a keyframe at every Ns wall-clock
                # boundary regardless of content, making segment cuts always clean.
                # -sc_threshold 0 disables scene-change forced keyframes so only
                # the -force_key_frames expression controls IDR placement.
                "-g", str(gop_frames),
                "-keyint_min", str(gop_frames),
                # ffmpeg expression vars: t=elapsed seconds, n_forced=count so far.
                "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECS})",
                "-sc_threshold", "0",
                "-pix_fmt", "yuv420p",
                *audio_codec_args,
                *audio_map_args,
                "-f", "hls",
                "-hls_time", segment_seconds,
                # Explicit segment container (ErsatzTV OutputFormatConcatHls).
                "-hls_segment_type", "mpegts",
                # Keep a 10-segment window (~60 s at 6 s/segment). With delayed
                # live-edge trimming, visible_segments = hls_list_size -
                # diag_delay_segments, so a slightly larger window preserves
                # playable history while staying off the true live edge.
                "-hls_list_size", "10",
                # +live tells the muxer to manage the sliding window properly for a
                # live stream (ErsatzTV OutputFormatConcatHls uses segment_list_flags).
                "-segment_list_flags", "+live",
                # Flags (ErsatzTV OutputFormatConcatHls):
                #   delete_segments   – remove expired .ts files to avoid disk fill
                #   program_date_time – EXT-X-PROGRAM-DATE-TIME tags for DVR seek
                #   omit_endlist      – never write EXT-X-ENDLIST; keeps the stream
                #                       live even on clean ffmpeg shutdown
                #   discont_start     – EXT-X-DISCONTINUITY at the first segment so
                #                       players handle a pipeline restart gracefully
                #   independent_segments – all segments start on a keyframe (IDR)
                "-hls_flags", "delete_segments+program_date_time+omit_endlist+discont_start+independent_segments",
                # Epoch-based start number keeps EXT-X-MEDIA-SEQUENCE strictly
                # increasing across pipeline restarts so clients never stall waiting
                # for a sequence they already consumed.
                "-start_number", start_number,
                "-hls_segment_filename", str(OUTPUT_DIR / "guide_%d.ts"),
                str(playlist_path),
            ]

            try:
                renderer_popen = subprocess.Popen(
                    renderer_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                self.logger.error("pipeline", f"Failed to start renderer: {exc}")
                return

            try:
                ffmpeg_popen = subprocess.Popen(
                    ffmpeg_cmd,
                    stdin=renderer_popen.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except OSError as exc:
                _terminate_pid(renderer_popen.pid, renderer_popen, self.logger, "renderer")
                self.logger.error("pipeline", f"Failed to start ffmpeg: {exc}")
                return

            # Close the parent's copy of the read-end so that when ffmpeg
            # exits the renderer gets SIGPIPE and can shut down cleanly.
            renderer_popen.stdout.close()
            renderer_popen.stdout = None

            self._renderer_popen = renderer_popen
            self._ffmpeg_popen = ffmpeg_popen
            self._renderer_pid = renderer_popen.pid
            self._ffmpeg_pid = ffmpeg_popen.pid
            _save_pid(RENDERER_PID_FILE, renderer_popen.pid)
            _save_pid(FFMPEG_PID_FILE, ffmpeg_popen.pid)
            self.logger.info(
                "pipeline",
                f"Renderer and FFmpeg pipeline started "
                f"(renderer PID {renderer_popen.pid}, ffmpeg PID {ffmpeg_popen.pid})",
            )

        # Start stderr-draining threads outside the lock so that log calls
        # (which write to SQLite) do not hold the pipeline lock.
        _start_stderr_reader(renderer_popen, "renderer", self.logger)
        _start_stderr_reader(ffmpeg_popen, "ffmpeg", self.logger)
        self._start_hls_telemetry_monitor(float(segment_seconds))

    def _stop_pipeline_locked(self) -> None:
        """Terminate pipeline processes.  Must be called with _lock held."""
        self._stop_hls_telemetry_monitor()
        # Kill the consumer (ffmpeg) first, then the producer (renderer).
        for pid, popen, label in [
            (self._ffmpeg_pid, self._ffmpeg_popen, "ffmpeg"),
            (self._renderer_pid, self._renderer_popen, "renderer"),
        ]:
            if pid is None:
                continue
            if popen is not None and popen.poll() is not None:
                # Owned process has already exited (possibly a zombie waiting
                # to be reaped).  Skip signalling and just reap it.
                try:
                    popen.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            elif _pid_alive(pid):
                _terminate_pid(pid, popen, self.logger, label)

        RENDERER_PID_FILE.unlink(missing_ok=True)
        FFMPEG_PID_FILE.unlink(missing_ok=True)

        self._renderer_pid = None
        self._ffmpeg_pid = None
        self._renderer_popen = None
        self._ffmpeg_popen = None

    def stop_pipeline(self) -> None:
        with self._lock:
            self._stop_pipeline_locked()
            self._clean_output_dir()
        self._pipeline_active = False
        # Regenerate standby.ts with the guide title so the standby screen
        # shows the configured title rather than the stale "Guide is Loading…"
        # message left over from the last start_pipeline call.
        config = self.store.get_config()
        title = (config.get("title") or "").strip() or "Retro Guide"
        self._generate_standby_segment(title)
        self.logger.info("pipeline", "Pipeline stopped; standby mode active")

    def restart_pipeline(self) -> None:
        self.refresh_state()
        self.start_pipeline(message="Guide is Restarting...")
        self.logger.info("pipeline", "Pipeline restarted")

    def ensure_pipeline_running(self) -> None:
        # Do nothing until the admin has explicitly started the pipeline.
        # This keeps the standby playlist visible on startup without any
        # auto-start behaviour.
        if not self._pipeline_active:
            return
        # Use popen.poll() for processes we own: os.kill(pid, 0) returns 0
        # for zombie processes on Linux (process table entry still exists),
        # so _pid_alive() would incorrectly report a dead process as alive.
        # popen.poll() correctly returns the exit code for exited/zombie
        # processes and also reaps them, preventing the pipeline from staying
        # dead indefinitely after a crash.
        if self._renderer_popen is not None:
            renderer_rc = self._renderer_popen.poll()
            renderer_dead = renderer_rc is not None
        else:
            renderer_rc = None
            renderer_dead = self._renderer_pid is None or not _pid_alive(self._renderer_pid)

        if self._ffmpeg_popen is not None:
            ffmpeg_rc = self._ffmpeg_popen.poll()
            ffmpeg_dead = ffmpeg_rc is not None
        else:
            ffmpeg_rc = None
            ffmpeg_dead = self._ffmpeg_pid is None or not _pid_alive(self._ffmpeg_pid)

        if renderer_dead or ffmpeg_dead:
            parts = []
            if renderer_dead:
                rc_str = str(renderer_rc) if renderer_rc is not None else "unknown"
                parts.append(f"renderer (PID {self._renderer_pid}, exit {rc_str})")
            if ffmpeg_dead:
                rc_str = str(ffmpeg_rc) if ffmpeg_rc is not None else "unknown"
                parts.append(f"ffmpeg (PID {self._ffmpeg_pid}, exit {rc_str})")
            self.logger.warning("pipeline", f"Dead process(es): {', '.join(parts)} — restarting pipeline")
            self.start_pipeline(message="Guide is Restarting...")

    def is_guide_buffered(self, min_secs: float = 25.0, min_segments: int = 5) -> bool:
        """Return True when the live guide has enough buffer to play smoothly.

        The check has two parts:

        1. **Segment count** – ``guide.m3u8`` must list at least *min_segments*
           real segment entries (default: 5).  Reading the playlist is the
           authoritative source of truth: it reflects what the player can
           actually download, is unaffected by ``delete_segments`` removing old
           files from disk, and avoids the race condition of counting files
           between ffmpeg writes.

        2. **Minimum age** – if the pipeline was *freshly started* (not
           reattached after a Flask restart), we also require *min_secs*
           seconds to have elapsed since ``start_pipeline()`` was called
           (default: 25 s, roughly 24–30 s of content at the default 6 s/seg).
           This gives the player time to fetch and buffer the first segments
           even if ffmpeg produced them quickly.

        Returns False (→ keep showing standby) when either condition is unmet.
        """
        guide_path = OUTPUT_DIR / "guide.m3u8"
        try:
            playlist_text = guide_path.read_text(encoding="utf-8")
            # Count lines that reference a real guide segment (not standby.ts).
            seg_count = sum(
                1
                for line in playlist_text.splitlines()
                if line.strip().endswith(".ts") and "standby" not in line
            )
        except OSError:
            return False

        if seg_count < min_segments:
            return False

        # For a freshly started pipeline also enforce a wall-clock minimum.
        if self._pipeline_started_at is not None:
            age = time.time() - self._pipeline_started_at
            if age < min_secs:
                return False

        return True

    def pipeline_needs_restart(self, old_config: dict, new_config: dict) -> bool:
        """Return True only when ffmpeg-level parameters that require tearing
        down and rebuilding the encoding pipeline have changed.  All other
        settings (theme, title, guide content, page timing, etc.) are picked
        up automatically by the renderer on the next frame without a restart.
        """
        pipeline_keys = {
            "resolution", "fps", "segment_seconds",
            "music_mode", "music_loop", "music_single_file", "music_playlist_files",
        }
        return any(old_config.get(k) != new_config.get(k) for k in pipeline_keys)

    def status(self) -> dict:
        config = self.store.get_config()
        # Compute buffered state using config-based thresholds (same values that
        # the HLS endpoints use so the version counter and endpoints agree).
        try:
            min_secs = float(config.get("diag_min_buffer_secs"))
        except (TypeError, ValueError):
            min_secs = 18.0
        try:
            min_segments = int(config.get("diag_min_buffer_segments"))
        except (TypeError, ValueError):
            min_segments = 3
        now_buffered = self._pipeline_active and self.is_guide_buffered(
            min_secs=min_secs, min_segments=min_segments
        )
        # Update stream version whenever the live/standby state flips.
        with self._lock:
            if now_buffered != self._last_was_buffered:
                self._stream_version += 1
                self._last_was_buffered = now_buffered
            version = self._stream_version
        return {
            "renderer_running": self._renderer_pid is not None and _pid_alive(self._renderer_pid),
            "ffmpeg_running": self._ffmpeg_pid is not None and _pid_alive(self._ffmpeg_pid),
            "pipeline_active": self._pipeline_active,
            # guide_buffered: True once the pipeline has enough HLS buffer to
            # serve real guide content.  Used by master.m3u8 to choose the
            # correct variant URL and by the frontend to trigger a reload.
            "guide_buffered": now_buffered,
            # stream_version increments on every standby↔live transition so
            # frontends can detect the switch without parsing playlist content.
            "stream_version": version,
            "last_refresh_status": self.last_refresh_status,
            "current_theme": config.get("theme"),
            "playlist_source": config.get("playlist_source"),
            "xmltv_source": config.get("xmltv_source"),
            # stream_url now points to the master playlist so both the admin
            # preview and IPTV clients use the same two-level HLS hierarchy.
            "stream_url": "/hls/master.m3u8",
        }
