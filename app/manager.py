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
from dataclasses import replace
from pathlib import Path
from typing import Optional

from werkzeug.utils import secure_filename

from .config_store import ConfigStore
from .ffmpeg_profiles import FFmpegProfile, normalize_hardware_acceleration_mode, resolve_ffmpeg_profile
from .gpu_capabilities import detect_gpu_capabilities
from .guide_state import STATE_PATH, SECONDARY_STATE_PATH, build_state, _scaled_secondary_theme
from .guide_preview import calculate_preview_layout, effective_preview_aspect_ratio, normalize_preview_audio_mode, resolve_preview_source
from .logging_utils import AppLogger
from .m3u_parser import parse_m3u
from .xmltv_parser import parse_xmltv

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"
MUSIC_DIR = DATA_DIR / "music"
WEATHER_MUSIC_DIR = DATA_DIR / "weather_music"
GUIDE_PREVIEW_DIR = DATA_DIR / "guide_preview"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
WEATHER_MUSIC_DIR.mkdir(parents=True, exist_ok=True)
GUIDE_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

STANDBY_SEGMENT = OUTPUT_DIR / "standby.ts"
STATIC_SEGMENT = OUTPUT_DIR / "static.ts"
STANDBY_PATTERN_DIR = DATA_DIR / "standby_patterns"

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

# Hardware-encoder runtime fallback.
# If the ffmpeg process dies this many times consecutively within the quick-
# failure window while a hardware encoder is selected, both managers force
# software (libx264) encoding for the remainder of the process lifetime and
# log a warning so the operator knows why the encoder changed.
HW_ENCODER_MAX_CONSECUTIVE_FAILURES = 3
# Seconds: an ffmpeg death within this window of the previous start_pipeline
# call is treated as a "quick" (likely encoder-init) failure.
HW_ENCODER_QUICK_FAILURE_WINDOW_SECS = 10.0

# PID files let us reattach to the pipeline after a Flask restart without
# killing the already-running renderer and ffmpeg processes.
RENDERER_PID_FILE = DATA_DIR / "renderer.pid"
FFMPEG_PID_FILE = DATA_DIR / "ffmpeg.pid"


# ---------------------------------------------------------------------------
# Low-level PID helpers
# ---------------------------------------------------------------------------

def playlist_path_has_segments(path: Path, prefix: str) -> bool:
    """Return True if an HLS playlist contains at least one real segment."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        line.strip().startswith(prefix) and line.strip().endswith(".ts")
        for line in text.splitlines()
    )


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
    """Return a PIL Image with SMPTE-style colour bars and overlay text."""
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

    _draw_standby_overlay(img, width, height, title, bar_h, band_opacity_percent=100)
    return img


def _draw_standby_overlay(
    img,
    width: int,
    height: int,
    title: str,
    bar_h: int,
    *,
    band_opacity_percent: int = 100,
) -> None:
    """Draw the standby title/subtitle text block over the lower band area."""
    from PIL import Image, ImageDraw

    opacity = max(0, min(100, int(band_opacity_percent)))
    if opacity >= 100:
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, bar_h, width - 1, height - 1], fill=(15, 15, 15))
    else:
        alpha = int(255 * (opacity / 100.0))
        base = img.convert("RGBA")
        overlay = Image.new("RGBA", (width, max(1, height - bar_h)), (15, 15, 15, alpha))
        base.alpha_composite(overlay, dest=(0, bar_h))
        img.paste(base.convert("RGB"))
        draw = ImageDraw.Draw(img)

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


def _build_custom_standby_image(
    path: Path,
    width: int,
    height: int,
    title: str,
    *,
    overlay_enabled: bool = True,
    overlay_opacity_percent: int = 50,
) -> "Image.Image":
    """Build a standby frame from a custom image and apply the standard overlay."""
    from PIL import Image, ImageOps

    with Image.open(path) as src:
        img = ImageOps.fit(src.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
    if overlay_enabled:
        _draw_standby_overlay(
            img,
            width,
            height,
            title,
            int(height * 0.75),
            band_opacity_percent=overlay_opacity_percent,
        )
    return img


def _build_static_noise_image(width: int, height: int) -> "Image.Image":
    """Return a PIL Image containing grayscale TV static noise."""
    from PIL import Image, ImageOps

    noise = Image.effect_noise((width, height), 72.0).convert("L")
    return ImageOps.autocontrast(noise).convert("RGB")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

def _build_audio_ffmpeg_args(
    config: dict,
    audio_codec: str,
    *,
    key_prefix: str = "music_",
    music_dir: Path | None = None,
    playlist_filename: str = "music_playlist.txt",
    input_index_start: int = 1,
    include_video_map: bool = True,
) -> tuple[list[str], list[str], list[str]]:
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
    silence_codec = ["-c:a", audio_codec, "-b:a", "32k"]
    video_map = ["-map", "0:v"] if include_video_map else []
    silence_index = int(input_index_start)
    music_index = silence_index + 1
    silence_map = [*video_map, "-map", f"{silence_index}:a"]

    mode_key = f"{key_prefix}mode"
    loop_key = f"{key_prefix}loop"
    single_file_key = f"{key_prefix}single_file"
    playlist_files_key = f"{key_prefix}playlist_files"
    music_dir = music_dir or MUSIC_DIR

    music_mode = config.get(mode_key, "none")
    music_loop = bool(config.get(loop_key, False))

    if music_mode == "single":
        filename = config.get(single_file_key, "")
        if filename:
            file_path = music_dir / Path(filename).name
            if file_path.exists():
                if music_loop:
                    return (
                        ["-stream_loop", "-1", "-i", str(file_path)],
                        ["-c:a", audio_codec, "-b:a", "128k"],
                        [*video_map, "-map", f"{silence_index}:a"],
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
                        ["-c:a", audio_codec, "-b:a", "128k"],
                        [
                            "-filter_complex",
                            f"[{music_index}:a][{silence_index}:a]amix=inputs=2:duration=longest:normalize=0[outa]",
                            *video_map, "-map", "[outa]",
                        ],
                    )

    elif music_mode in ("playlist", "all"):
        if music_mode == "all":
            playlist_files = sorted(
                path.name for path in music_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac"}
            ) if music_dir.is_dir() else []
        else:
            playlist_files = config.get(playlist_files_key, [])
        valid_files = [
            music_dir / Path(f).name
            for f in playlist_files
            if (music_dir / Path(f).name).exists()
        ]
        if valid_files:
            concat_file = DATA_DIR / playlist_filename
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
                    ["-c:a", audio_codec, "-b:a", "128k"],
                    [*video_map, "-map", f"{silence_index}:a"],
                )
            else:
                # See normalize=0 comment above for the same pattern.
                return (
                    silence_input + concat_input,
                    ["-c:a", audio_codec, "-b:a", "128k"],
                    [
                        "-filter_complex",
                        f"[{music_index}:a][{silence_index}:a]amix=inputs=2:duration=longest:normalize=0[outa]",
                        *video_map, "-map", "[outa]",
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



def _build_guide_preview_ffmpeg_args(
    config: dict,
    source_override: str | None = None,
) -> tuple[list[str], list[str], list[str], bool]:
    """Return preview video input/filter/map args and whether preview is active.

    The renderer always owns the blue preview/information background. FFmpeg only
    decodes/scales/overlays the configured source when a valid source is available.
    A missing local file or invalid URL therefore leaves a clean themed information
    panel instead of changing renderer geometry or crashing command construction.
    """
    if not bool(config.get("guide_preview_enabled", False)):
        return [], [], [], False

    source = source_override if source_override is not None else resolve_preview_source(config, BASE_DIR)
    if not source:
        return [], [], [], False

    try:
        theme_name = str(config.get("theme", "retrostation_mc"))
        theme_path = BASE_DIR / "app" / "themes" / theme_name / "theme.json"
        theme = json.loads(theme_path.read_text(encoding="utf-8")) if theme_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        theme = {}
    # Secondary Guide output uses a scaled theme/layout. The FFmpeg overlay
    # must use the same geometry as the native secondary renderer or the
    # actual preview video will be smaller/misaligned inside the raised frame.
    if bool(config.get("_secondary_guide_output", False)):
        theme, _ = _scaled_secondary_theme(
            theme, str(config.get("resolution", "720x480"))
        )
    layout = calculate_preview_layout(
        str(config.get("resolution", "1280x720")),
        str(config.get("aspect_ratio", "16:9")),
        theme.get("layout", {}),
        effective_preview_aspect_ratio(config),
    )

    input_args: list[str] = []
    if source.startswith(("http://", "https://")):
        input_args.extend([
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ])
    else:
        input_args.extend(["-stream_loop", "-1"])
    input_args.extend(["-i", source])

    max_w = int(layout["preview_max_width"])
    max_h = int(layout["preview_max_height"])
    x = int(layout["preview_x"])
    y = int(layout["preview_y"])

    # The Guide manager may replace an external URL with a local normalized HLS
    # relay before this command is built.  Keep the overlay-side graph simple:
    # fixed dimensions/SAR only.  This prevents the VA-API Guide graph from ever
    # seeing the heterogeneous source's decoder-level format transitions.
    preview_filters: list[str] = []
    preview_filters.extend([
        f"scale={max_w}:{max_h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bilinear",
        "setsar=1",
        f"pad={max_w}:{max_h}:(ow-iw)/2:(oh-ih)/2:black",
    ])
    filter_graph = (
        f"[1:v]{','.join(preview_filters)}[guidepreview];"
        f"[0:v][guidepreview]overlay={x}:{y}:eof_action=pass:repeatlast=1[vout]"
    )
    return input_args, ["-filter_complex", filter_graph], ["-map", "[vout]"], True

def _secondary_guide_bitrate(resolution: str) -> str:
    """Conservative SD bitrate caps for low-power IPTV clients.

    Guide frames contain sharp text/edges that can create high instantaneous
    bitrates under CRF-only encoding. Capping the secondary output keeps HLS
    segments smaller and reduces boundary stalls on devices such as Raspberry
    Pi 3 while retaining ample quality for the selected raster sizes.
    """
    return {
        "640x480": "900k",
        "720x480": "1100k",
        "960x720": "1800k",
    }.get(str(resolution), "1100k")


def _build_ffmpeg_command(
    profile: FFmpegProfile,
    fps: str,
    audio_input_args: list[str],
    audio_codec_args: list[str],
    audio_map_args: list[str],
    start_number: str,
    playlist_path: Path,
    preview_input_args: list[str] | None = None,
    video_filter_args: list[str] | None = None,
    video_map_args: list[str] | None = None,
    segment_prefix: str = "guide",
) -> list[str]:
    segment_seconds = str(profile.hls_segment_length)
    # GOP size in frames. A 2-second keyframe interval keeps each segment
    # populated with multiple IDR points so segment boundaries can start cleanly
    # and avoid "grey frame" stalls in stricter IPTV clients.
    gop_frames = int(fps) * HLS_KEYFRAME_INTERVAL_SECS
    video_codec, video_codec_args, preset, tune, _, pix_fmt = _resolve_video_encoder_path(profile)
    hw_device_init_args = _resolve_hw_device_init_args(video_codec)
    preview_input_args = preview_input_args or []
    video_filter_args = video_filter_args or []
    video_map_args = video_map_args or []
    video_filter_args, video_map_args = _apply_hw_filter_to_guide(
        video_codec, video_filter_args, video_map_args
    )
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-y",
        *hw_device_init_args,
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", profile.resolution,
        "-r", fps,
        "-i", "-",
        *preview_input_args,
        *audio_input_args,
        *video_filter_args,
        "-c:v", video_codec,
    ]
    ffmpeg_cmd.extend(video_codec_args)
    if preset:
        ffmpeg_cmd.extend(["-preset", preset])
    if tune:
        ffmpeg_cmd.extend(["-tune", tune])
    ffmpeg_cmd.extend([
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
    ])
    if profile.bitrate:
        ffmpeg_cmd.extend(["-b:v", profile.bitrate, "-maxrate", profile.bitrate])
        try:
            if str(profile.bitrate).lower().endswith("k"):
                bufsize = f"{int(str(profile.bitrate)[:-1]) * 2}k"
            elif str(profile.bitrate).lower().endswith("m"):
                bufsize = f"{float(str(profile.bitrate)[:-1]) * 2:g}m"
            else:
                bufsize = str(int(profile.bitrate) * 2)
            ffmpeg_cmd.extend(["-bufsize", bufsize])
        except (TypeError, ValueError):
            pass
    if pix_fmt:
        ffmpeg_cmd.extend(["-pix_fmt", pix_fmt])
    ffmpeg_cmd.extend([
        *audio_codec_args,
        *video_map_args,
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
        "-hls_segment_filename", str(OUTPUT_DIR / f"{segment_prefix}_%d.ts"),
        str(playlist_path),
    ])
    return ffmpeg_cmd


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
        # Optional secondary Guide output uses its own native renderer and encoder.
        self._secondary_renderer_pid: Optional[int] = None
        self._secondary_renderer_popen: Optional[subprocess.Popen] = None
        self._secondary_ffmpeg_pid: Optional[int] = None
        self._secondary_ffmpeg_popen: Optional[subprocess.Popen] = None
        self._secondary_last_error: Optional[str] = None
        # External Guide preview normalization relay.  A single software FFmpeg
        # process absorbs HLS/programme format changes and produces one stable
        # local feed consumed by both primary and secondary Guide encoders.
        self._preview_normalizer_pid: Optional[int] = None
        self._preview_normalizer_popen: Optional[subprocess.Popen] = None
        self._preview_normalized_playlist: Optional[Path] = None
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
        self._last_hls_watchdog_warning_at: float | None = None
        self._last_hls_watchdog_warning_key: tuple[str, ...] = ()
        # Hardware-encoder runtime fallback state.
        # _hw_failure_count: consecutive quick-failure restarts while a hardware
        #   encoder was in use.  Reset to 0 after a long-lived run.
        # _hw_fallback_forced: set True once the failure threshold is hit; stays
        #   True for the lifetime of this process so we don't keep hammering
        #   broken hardware.
        # _last_encoder_type: "hardware" or "software" – set each time
        #   start_pipeline resolves the ffmpeg profile so ensure_pipeline_running
        #   can decide whether to count a crash as a hw failure.
        self._hw_failure_count: int = 0
        self._hw_fallback_forced: bool = False
        self._last_encoder_type: str = "software"

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
            self._generate_static_segment()
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
        if bool(config.get("guide_secondary_enabled", False)):
            secondary_resolution = str(config.get("guide_secondary_resolution", "720x480") or "720x480")
            if secondary_resolution not in {"960x720", "720x480", "640x480"}:
                secondary_resolution = "720x480"
            sec_w, sec_h = [int(part) for part in secondary_resolution.lower().split("x", 1)]
            # 640x480 and 960x720 are true square-pixel 4:3 targets. 720x480 is
            # retained for CRT/SD workflows and is laid out as a 4:3 Guide canvas.
            secondary_aspect = "4:3" if (sec_w / max(1, sec_h)) <= 1.5 else "16:9"
            secondary_config = {**config, "resolution": secondary_resolution, "aspect_ratio": secondary_aspect, "_secondary_guide_output": True}
            build_state(secondary_config, channels, programmes, output_path=SECONDARY_STATE_PATH)
        else:
            SECONDARY_STATE_PATH.unlink(missing_ok=True)
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

        ``standby.ts`` and ``static.ts`` are intentionally preserved across
        restarts so that the standby playlist is immediately available.

        ``weather.m3u8`` / ``weather_*.ts``, ``traffic.m3u8`` / ``traffic_*.ts``,
        ``news.m3u8`` / ``news_*.ts``, and the disk-backed Channel Mix files are
        owned independently and intentionally skipped here so guide restarts do
        not interrupt them.

        Epoch-based ``-start_number`` (set in ``start_pipeline``) ensures that
        ``EXT-X-MEDIA-SEQUENCE`` always advances across restarts, so there is no
        risk of the new playlist being mistaken for stale content.
        """
        for path in OUTPUT_DIR.iterdir():
            if path in {STANDBY_SEGMENT, STATIC_SEGMENT}:
                continue
            # Preserve virtual-channel HLS files managed independently.
            if (
                path.name in {"weather.m3u8", "traffic.m3u8", "news.m3u8", "channel-mix.m3u8", "channel-mix-source.m3u8"}
                or path.name.startswith("weather_")
                or path.name.startswith("traffic_")
                or path.name.startswith("news_")
                or path.name.startswith("channel_mix_")
            ):
                continue
            if path.suffix in (".ts", ".m3u8"):
                try:
                    path.unlink()
                except OSError as exc:
                    self.logger.warning("pipeline", f"Could not remove stale output file {path}: {exc}")

    def _is_valid_image_file(self, path: Path) -> bool:
        """Return True when *path* points to an image PIL can parse successfully."""
        try:
            from PIL import Image
            with Image.open(path) as img:
                img.verify()
            return True
        except Exception:
            return False

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
            custom_name = secure_filename(config.get("standby_custom_file", "") or "")
            custom_path = STANDBY_PATTERN_DIR / custom_name if custom_name else None
            overlay_enabled_raw = config.get("standby_overlay_enabled", True)
            if isinstance(overlay_enabled_raw, str):
                overlay_enabled = overlay_enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                overlay_enabled = bool(overlay_enabled_raw)
            overlay_opacity = max(0, min(100, int(config.get("standby_overlay_opacity", 50))))
            if custom_path and custom_path.is_file():
                img = _build_custom_standby_image(
                    custom_path,
                    width,
                    height,
                    title,
                    overlay_enabled=overlay_enabled,
                    overlay_opacity_percent=overlay_opacity,
                )
            else:
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

    def _generate_static_segment(self) -> None:
        """Encode a looping TV static segment into ``static.ts``."""
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
            img = _build_static_noise_image(width, height)
        except Exception as exc:
            self.logger.warning("pipeline", f"Could not build static image: {exc}")
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
                "-keyint_min", str(gop_size),
                "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "32k",
                "-map", "0:v", "-map", "1:a",
                "-t", str(STANDBY_DURATION_SECS),
                str(STATIC_SEGMENT),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                self.logger.warning("pipeline", f"Static segment encode failed (exit {result.returncode}): {stderr}")
            else:
                self.logger.info("pipeline", f"Static segment written to {STATIC_SEGMENT}")
        except Exception as exc:
            self.logger.warning("pipeline", f"Could not generate static segment: {exc}")
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def _cleanup_preview_normalizer_files(self) -> None:
        """Remove stale files from the shared external-preview normalization relay."""
        for path in GUIDE_PREVIEW_DIR.glob("normalized-preview-*.ts"):
            path.unlink(missing_ok=True)
        (GUIDE_PREVIEW_DIR / "normalized-preview.m3u8").unlink(missing_ok=True)

    def _start_preview_normalizer_locked(self, config: dict) -> str | None:
        """Start one stable local HLS relay for an external Guide preview URL.

        The Guide VA-API encoders must not decode heterogeneous external HLS
        directly.  Source transitions can change fps, colour metadata, dimensions,
        or other decoder-visible properties and trigger libavfilter reinitialisation
        across hwupload.  This software-only relay owns that instability and emits
        a fixed 15-fps/yuv420p/BT.709-tagged canvas for both Guide outputs.
        """
        source = resolve_preview_source(config, BASE_DIR)
        if not source or not source.startswith(("http://", "https://")):
            return None

        self._cleanup_preview_normalizer_files()
        playlist = GUIDE_PREVIEW_DIR / "normalized-preview.m3u8"
        segment_pattern = GUIDE_PREVIEW_DIR / "normalized-preview-%06d.ts"

        aspect = str(effective_preview_aspect_ratio(config) or "16:9").strip().lower()
        if aspect.startswith("4") or aspect in {"1.333", "1.33", "4/3"}:
            canvas_w, canvas_h = 640, 480
        else:
            canvas_w, canvas_h = 640, 360
        fps = str(config.get("fps") or 15)
        try:
            gop = max(1, int(round(float(fps))))
        except (TypeError, ValueError):
            fps = "15"
            gop = 15

        vf = (
            f"fps={fps},"
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease:"
            "force_divisible_by=2:flags=bilinear,"
            "format=yuv420p,setsar=1,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black,"
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
            "-i", source,
            "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p", "-g", str(gop), "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "8",
            "-hls_flags", "delete_segments+omit_endlist+independent_segments+program_date_time",
            "-hls_segment_filename", str(segment_pattern),
            str(playlist),
        ]
        try:
            popen = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            self.logger.error("guide-preview-normalizer", f"Failed to start preview normalizer: {exc}")
            return None

        self._preview_normalizer_popen = popen
        self._preview_normalizer_pid = popen.pid
        self._preview_normalized_playlist = playlist
        _start_stderr_reader(popen, "guide-preview-normalizer", self.logger)

        # Do not start either Guide consumer until the relay has published at least
        # one segment.  This avoids treating a not-yet-created local playlist as an
        # input failure during normal startup.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if popen.poll() is not None:
                self.logger.error(
                    "guide-preview-normalizer",
                    f"Preview normalizer exited during startup with status {popen.returncode}.",
                )
                self._preview_normalizer_pid = None
                self._preview_normalizer_popen = None
                self._preview_normalized_playlist = None
                return None
            if playlist.is_file() and any(GUIDE_PREVIEW_DIR.glob("normalized-preview-*.ts")):
                self.logger.info(
                    "guide-preview-normalizer",
                    f"External preview normalized to {canvas_w}x{canvas_h}@{fps} fps for shared HD/SD Guide ingest "
                    f"(PID {popen.pid}).",
                )
                return str(playlist)
            time.sleep(0.05)

        self.logger.error("guide-preview-normalizer", "Timed out waiting for normalized preview HLS startup.")
        _terminate_pid(popen.pid, popen, self.logger, "guide-preview-normalizer")
        self._preview_normalizer_pid = None
        self._preview_normalizer_popen = None
        self._preview_normalized_playlist = None
        self._cleanup_preview_normalizer_files()
        return None

    def start_pipeline(self, message: str = "Guide is Loading...") -> None:
        # Mark the pipeline as intentionally active so the background worker
        # will restart it automatically if it ever crashes.
        self._pipeline_active = True
        # _pipeline_started_at is recorded when the live ffmpeg process is
        # actually spawned.  Starting this timer before standby generation can
        # hide an immediate hardware-encoder crash from the fallback logic.

        # Generate (or refresh) the standby segment before tearing down the
        # running pipeline so that standby.ts is ready the moment guide.m3u8
        # disappears.  Done outside the lock; FFmpeg can take a second or two.
        self._generate_standby_segment(message)
        self._generate_static_segment()

        secondary_start = None
        with self._lock:
            self._stop_pipeline_locked()
            self._clean_output_dir()

            config = self.store.get_config()
            if self._hw_fallback_forced:
                config = {**config, "hardware_acceleration_mode": "software_fallback"}
                self.logger.warning(
                    "pipeline",
                    f"Hardware encoder failed {HW_ENCODER_MAX_CONSECUTIVE_FAILURES} consecutive time(s) quickly; "
                    "forcing software fallback (libx264) for the remainder of this session.",
                )
            try:
                gpu_capabilities = detect_gpu_capabilities()
            except Exception as exc:
                self.logger.warning("pipeline", f"GPU detection unavailable; using software fallback profile: {exc}")
                gpu_capabilities = {}
            profile = resolve_ffmpeg_profile(config, gpu_capabilities)
            self._last_encoder_type = profile.encoder_type
            selected_codec, _, _, _, encoder_path, _ = _resolve_video_encoder_path(profile)
            self.logger.info(
                "pipeline",
                f"Selected encoder path: {encoder_path} (codec={selected_codec}, provider={profile.hardware_acceleration_provider or 'software'})",
            )
            resolution = profile.resolution
            fps = str(config.get("fps", 15))
            segment_seconds = str(profile.hls_segment_length)
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

            raw_preview_source = resolve_preview_source(config, BASE_DIR)
            external_preview = bool(raw_preview_source and raw_preview_source.startswith(("http://", "https://")))
            normalized_preview_source = self._start_preview_normalizer_locked(config) if external_preview else None
            # For external URLs, never silently fall back to direct ingest if the
            # normalizer cannot start; that is the exact crash path this relay
            # exists to isolate.  An empty override leaves the themed preview area
            # visible without video until the pipeline is restarted successfully.
            preview_source_override = (normalized_preview_source or "") if external_preview else None
            preview_input_args, video_filter_args, video_map_args, preview_active = _build_guide_preview_ffmpeg_args(
                config, source_override=preview_source_override
            )
            preview_audio_mode = normalize_preview_audio_mode(config.get("guide_preview_audio_mode"))

            preview_enabled = bool(config.get("guide_preview_enabled", False))
            if preview_active and preview_audio_mode == "preview":
                # Preview video is input #1. Optional mapping keeps video-only
                # sources from preventing the Guide Channel from starting.
                audio_input_args = []
                audio_codec_args = ["-c:a", profile.audio_codec, "-b:a", "128k"]
                audio_map_args = ["-map", "1:a:0?"]
            elif preview_active:
                # With an active preview input, all non-preview audio begins at
                # input #2. Silent explicitly suppresses normal Guide music;
                # Guide keeps the existing Guide Channel music selection.
                audio_config = {**config, "music_mode": "none"} if preview_audio_mode == "silent" else config
                audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(
                    audio_config,
                    profile.audio_codec,
                    input_index_start=2,
                    include_video_map=False,
                )
            else:
                # No valid preview source. When the preview feature is enabled,
                # Preview audio has nothing to play and Silent must remain
                # silent; both therefore use the compatibility silence track.
                # Guide continues to use the normal Guide Channel audio path.
                audio_config = (
                    {**config, "music_mode": "none"}
                    if preview_enabled and preview_audio_mode in {"preview", "silent"}
                    else config
                )
                audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(
                    audio_config, profile.audio_codec
                )

            ffmpeg_cmd = _build_ffmpeg_command(
                profile,
                fps,
                audio_input_args,
                audio_codec_args,
                audio_map_args,
                start_number,
                playlist_path,
                preview_input_args=preview_input_args,
                video_filter_args=video_filter_args,
                video_map_args=video_map_args,
            )
            if bool(config.get("guide_preview_enabled", False)):
                if preview_active:
                    self.logger.info(
                        "pipeline",
                        f"Guide preview enabled: audio={preview_audio_mode}, source={resolve_preview_source(config, BASE_DIR)!r}",
                    )
                else:
                    self.logger.warning(
                        "pipeline",
                        "Guide preview layout enabled but no valid preview source is configured; showing themed information area without video.",
                    )

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
            self._pipeline_started_at = time.time()

            # Optional secondary Guide output: render natively at the selected
            # resolution instead of scaling the completed primary 16:9 HLS. This
            # preserves a true 4:3 Guide layout for 960x720 and 640x480 CRT use.
            if bool(config.get("guide_secondary_enabled", False)):
                secondary_resolution = str(config.get("guide_secondary_resolution", "720x480") or "720x480")
                if secondary_resolution not in {"960x720", "720x480", "640x480"}:
                    secondary_resolution = "720x480"
                sec_w, sec_h = [int(part) for part in secondary_resolution.lower().split("x", 1)]
                secondary_aspect = "4:3" if (sec_w / max(1, sec_h)) <= 1.5 else "16:9"
                secondary_config = {**config, "resolution": secondary_resolution, "aspect_ratio": secondary_aspect, "_secondary_guide_output": True}
                secondary_profile = replace(profile, resolution=secondary_resolution, bitrate=_secondary_guide_bitrate(secondary_resolution))
                sec_codec, _, _, _, sec_path, _ = _resolve_video_encoder_path(secondary_profile)

                secondary_renderer_cmd = [
                    sys.executable, str(BASE_DIR / "app" / "renderer.py"),
                    "--state", str(SECONDARY_STATE_PATH),
                    "--fps", fps,
                    "--resolution", secondary_resolution,
                ]
                if self._telemetry_debug:
                    secondary_renderer_cmd.append("--telemetry")

                sec_preview_input_args, sec_video_filter_args, sec_video_map_args, sec_preview_active = _build_guide_preview_ffmpeg_args(
                    secondary_config, source_override=preview_source_override
                )
                if sec_preview_active and preview_audio_mode == "preview":
                    sec_audio_input_args = []
                    sec_audio_codec_args = ["-c:a", secondary_profile.audio_codec, "-b:a", "128k"]
                    sec_audio_map_args = ["-map", "1:a:0?"]
                elif sec_preview_active:
                    sec_audio_config = {**secondary_config, "music_mode": "none"} if preview_audio_mode == "silent" else secondary_config
                    sec_audio_input_args, sec_audio_codec_args, sec_audio_map_args = _build_audio_ffmpeg_args(
                        sec_audio_config, secondary_profile.audio_codec, input_index_start=2, include_video_map=False
                    )
                else:
                    sec_audio_config = (
                        {**secondary_config, "music_mode": "none"}
                        if preview_enabled and preview_audio_mode in {"preview", "silent"}
                        else secondary_config
                    )
                    sec_audio_input_args, sec_audio_codec_args, sec_audio_map_args = _build_audio_ffmpeg_args(
                        sec_audio_config, secondary_profile.audio_codec
                    )

                sec_playlist = OUTPUT_DIR / "guide-secondary.m3u8"
                sec_cmd = _build_ffmpeg_command(
                    secondary_profile, fps, sec_audio_input_args, sec_audio_codec_args, sec_audio_map_args,
                    start_number, sec_playlist,
                    preview_input_args=sec_preview_input_args,
                    video_filter_args=sec_video_filter_args,
                    video_map_args=sec_video_map_args,
                    segment_prefix="guide_secondary",
                )
                try:
                    secondary_renderer_popen = subprocess.Popen(
                        secondary_renderer_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
                    )
                    secondary_ffmpeg_popen = subprocess.Popen(
                        sec_cmd, stdin=secondary_renderer_popen.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True
                    )
                    secondary_renderer_popen.stdout.close()
                    secondary_renderer_popen.stdout = None
                    self._secondary_renderer_popen = secondary_renderer_popen
                    self._secondary_renderer_pid = secondary_renderer_popen.pid
                    self._secondary_ffmpeg_popen = secondary_ffmpeg_popen
                    self._secondary_ffmpeg_pid = secondary_ffmpeg_popen.pid
                    self._secondary_last_error = None
                    _start_stderr_reader(secondary_renderer_popen, "secondary-guide-renderer", self.logger)
                    _start_stderr_reader(secondary_ffmpeg_popen, "secondary-guide-ffmpeg", self.logger)
                    self.logger.info(
                        "pipeline",
                        f"Secondary Guide native renderer started at {secondary_resolution} ({secondary_aspect}) using {sec_path} "
                        f"(renderer PID {secondary_renderer_popen.pid}, ffmpeg PID {secondary_ffmpeg_popen.pid})",
                    )
                except OSError as exc:
                    self._secondary_last_error = str(exc)
                    if 'secondary_ffmpeg_popen' in locals():
                        _terminate_pid(secondary_ffmpeg_popen.pid, secondary_ffmpeg_popen, self.logger, "secondary-guide-ffmpeg")
                    if 'secondary_renderer_popen' in locals():
                        _terminate_pid(secondary_renderer_popen.pid, secondary_renderer_popen, self.logger, "secondary-guide-renderer")
                    self.logger.error("pipeline", f"Failed to start native secondary Guide output: {exc}")

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
            (self._secondary_ffmpeg_pid, self._secondary_ffmpeg_popen, "secondary-guide-ffmpeg"),
            (self._secondary_renderer_pid, self._secondary_renderer_popen, "secondary-guide-renderer"),
            (self._ffmpeg_pid, self._ffmpeg_popen, "ffmpeg"),
            (self._renderer_pid, self._renderer_popen, "renderer"),
            (self._preview_normalizer_pid, self._preview_normalizer_popen, "guide-preview-normalizer"),
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
        self._secondary_renderer_pid = None
        self._secondary_renderer_popen = None
        self._secondary_ffmpeg_pid = None
        self._secondary_ffmpeg_popen = None
        self._preview_normalizer_pid = None
        self._preview_normalizer_popen = None
        self._preview_normalized_playlist = None
        self._cleanup_preview_normalizer_files()

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
        self._generate_static_segment()
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

            # Track consecutive quick failures while a hardware encoder is in
            # use so we can force a software fallback once the threshold is hit.
            elapsed = (
                time.time() - self._pipeline_started_at
                if self._pipeline_started_at is not None
                else float("inf")
            )
            guide_had_output = False
            try:
                guide_had_output = playlist_path_has_segments(OUTPUT_DIR / "guide.m3u8", "guide_")
            except Exception:
                guide_had_output = False
            if self._last_encoder_type == "hardware" and (
                elapsed < HW_ENCODER_QUICK_FAILURE_WINDOW_SECS or not guide_had_output
            ):
                self._hw_failure_count += 1
                if self._hw_failure_count >= HW_ENCODER_MAX_CONSECUTIVE_FAILURES and not self._hw_fallback_forced:
                    self._hw_fallback_forced = True
                    self.logger.warning(
                        "pipeline",
                        f"Hardware encoder has failed {self._hw_failure_count} time(s) before establishing stable HLS output; "
                        "switching to software (libx264) fallback.",
                    )
            else:
                # Ran long enough (or was already on software) — reset the counter.
                self._hw_failure_count = 0

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
            "guide_preview_enabled", "guide_preview_source_type", "guide_preview_file",
            "guide_preview_url", "guide_preview_url_channel", "guide_preview_url_channel_name", "guide_preview_audio_mode",
            "guide_preview_aspect_mode", "guide_preview_detected_aspect_ratio", "guide_preview_detected_source_key",
        }
        return any(old_config.get(k) != new_config.get(k) for k in pipeline_keys)

    def _hls_watchdog_status(self, config: dict) -> dict:
        """Return HLS continuity diagnostics for status()/diagnostics UI.

        Keys include health/state, playlist and latest-segment timestamps/ages,
        stale threshold, warning codes, and restart hook readiness signals.
        """
        guide_path = OUTPUT_DIR / "guide.m3u8"
        try:
            segment_target = max(1.0, float(config.get("segment_seconds", 6)))
        except (TypeError, ValueError):
            segment_target = 6.0
        stale_threshold = round(segment_target * 3.0, 3)
        now = time.time()

        if not self._pipeline_active:
            return {
                "healthy": True,
                "state": "idle",
                "playlist_updated_at": None,
                "playlist_age_secs": None,
                "latest_segment": None,
                "latest_segment_updated_at": None,
                "latest_segment_age_secs": None,
                "stale_threshold_secs": stale_threshold,
                "warnings": [],
                "restart_hook_ready": True,
                "restart_recommended": False,
            }

        try:
            playlist_stat = guide_path.stat()
            playlist_updated_at = datetime.fromtimestamp(playlist_stat.st_mtime, tz=timezone.utc).isoformat()
            playlist_age = max(0.0, now - playlist_stat.st_mtime)
            lines = guide_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            warnings = ["playlist_missing_or_unreadable"]
            return {
                "healthy": False,
                "state": "degraded",
                "playlist_updated_at": None,
                "playlist_age_secs": None,
                "latest_segment": None,
                "latest_segment_updated_at": None,
                "latest_segment_age_secs": None,
                "stale_threshold_secs": stale_threshold,
                "warnings": warnings,
                "restart_hook_ready": True,
                "restart_recommended": True,
            }

        segment_name: str | None = None
        for raw in lines:
            line = raw.strip()
            if line and not line.startswith("#"):
                # Keep the most recent segment entry in the active playlist window.
                segment_name = line.split("?", 1)[0]

        segment_updated_at = None
        segment_age = None
        if segment_name:
            segment_path = OUTPUT_DIR / Path(segment_name).name
            try:
                segment_stat = segment_path.stat()
                segment_updated_at = datetime.fromtimestamp(segment_stat.st_mtime, tz=timezone.utc).isoformat()
                segment_age = max(0.0, now - segment_stat.st_mtime)
            except OSError:
                segment_age = None

        warnings: list[str] = []
        if playlist_age > stale_threshold:
            warnings.append("playlist_updates_stalled")
        if not segment_name:
            warnings.append("playlist_window_empty")
        elif segment_age is None:
            warnings.append("latest_segment_missing")
        elif segment_age > stale_threshold:
            warnings.append("segment_generation_stalled")
        if playlist_age > (stale_threshold * 2.0):
            warnings.append("playlist_window_stale")

        if warnings:
            warning_key = tuple(warnings)
            throttle_check_time = time.monotonic()
            should_log = (
                warning_key != self._last_hls_watchdog_warning_key
                or self._last_hls_watchdog_warning_at is None
                or (throttle_check_time - self._last_hls_watchdog_warning_at) >= stale_threshold
            )
            if should_log:
                self._last_hls_watchdog_warning_key = warning_key
                self._last_hls_watchdog_warning_at = throttle_check_time
                self.logger.warning(
                    "hls.watchdog",
                    json.dumps(
                        {
                            "type": "hls_watchdog_warning",
                            "warnings": warnings,
                            "playlist_age_secs": round(playlist_age, 3),
                            "latest_segment_age_secs": round(segment_age, 3) if segment_age is not None else None,
                            "stale_threshold_secs": stale_threshold,
                        },
                        sort_keys=True,
                    ),
                )
        else:
            self._last_hls_watchdog_warning_key = ()

        return {
            "healthy": not warnings,
            "state": "healthy" if not warnings else "degraded",
            "playlist_updated_at": playlist_updated_at,
            "playlist_age_secs": round(playlist_age, 3),
            "latest_segment": segment_name,
            "latest_segment_updated_at": segment_updated_at,
            "latest_segment_age_secs": round(segment_age, 3) if segment_age is not None else None,
            "stale_threshold_secs": stale_threshold,
            "warnings": warnings,
            "restart_hook_ready": True,
            "restart_recommended": bool(warnings),
        }

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
        hls_watchdog = self._hls_watchdog_status(config)
        try:
            gpu_capabilities = detect_gpu_capabilities()
        except Exception as exc:
            gpu_capabilities = {
                "running_in_docker": False,
                "hardware_available": False,
                "device_detected_providers": [],
                "detected_hardware_providers": [],
                "docker_visible_providers": [],
                "ffmpeg_available": False,
                "ffmpeg_error": str(exc),
                "ffmpeg_detected_encoders": [],
                "providers": {},
                "software_fallback": {
                    "available": True,
                    "label": "software",
                    "reason": "Software fallback is always available (libx264).",
                },
                "message": "GPU detection failed; software fallback is active.",
            }
        gpu_capabilities["active_path"] = _build_active_gpu_path(config, gpu_capabilities)
        return {
            "renderer_running": self._renderer_pid is not None and _pid_alive(self._renderer_pid),
            "ffmpeg_running": self._ffmpeg_pid is not None and _pid_alive(self._ffmpeg_pid),
            "secondary_renderer_running": self._secondary_renderer_pid is not None and _pid_alive(self._secondary_renderer_pid),
            "secondary_renderer_pid": self._secondary_renderer_pid,
            "secondary_ffmpeg_running": self._secondary_ffmpeg_pid is not None and _pid_alive(self._secondary_ffmpeg_pid),
            "secondary_ffmpeg_pid": self._secondary_ffmpeg_pid,
            "secondary_guide_enabled": bool(config.get("guide_secondary_enabled", False)),
            "secondary_guide_resolution": config.get("guide_secondary_resolution", "720x480"),
            "secondary_guide_playlist": "/hls/guide-secondary.m3u8" if bool(config.get("guide_secondary_enabled", False)) else None,
            "secondary_guide_ready": (OUTPUT_DIR / "guide-secondary.m3u8").exists(),
            "secondary_guide_error": self._secondary_last_error,
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
            "hls_watchdog": hls_watchdog,
            "gpu_capabilities": gpu_capabilities,
        }


def _build_active_gpu_path(config: dict, gpu_capabilities: dict | None) -> dict:
    profile = resolve_ffmpeg_profile(config, gpu_capabilities)
    provider_name = str(profile.hardware_acceleration_provider or "software")
    using_hardware = provider_name != "software"
    normalized_mode = normalize_hardware_acceleration_mode(config.get("hardware_acceleration_mode"))
    detected_devices = []
    ready_hardware_providers: list[str] = []
    if isinstance(gpu_capabilities, dict):
        raw_detected_devices = gpu_capabilities.get("device_detected_providers")
        if isinstance(raw_detected_devices, list):
            detected_devices = [str(provider) for provider in raw_detected_devices]
        raw_ready = gpu_capabilities.get("detected_hardware_providers")
        if isinstance(raw_ready, list):
            ready_hardware_providers = [str(provider) for provider in raw_ready]

    # Build a human-readable list of hardware provider labels that are both
    # detected AND encoder-ready (passed the functional probe).
    ready_hw_labels: list[str] = []
    if ready_hardware_providers and isinstance(gpu_capabilities, dict):
        providers_map = gpu_capabilities.get("providers") or {}
        for ready_provider in ready_hardware_providers:
            info = providers_map.get(ready_provider) if isinstance(providers_map, dict) else None
            hw_label = (info.get("label") if isinstance(info, dict) else None) or ready_provider
            ready_hw_labels.append(str(hw_label))

    if using_hardware:
        label = _hardware_provider_label(gpu_capabilities, provider_name)
        reason = "Using the detected hardware encoder."
    elif normalized_mode != "hardware_if_available":
        if ready_hw_labels:
            # Hardware is available and ready, but the admin has chosen forced software.
            hw_names = ", ".join(ready_hw_labels)
            label = f"Software fallback (libx264) — {hw_names} encoder-ready"
            reason = (
                f"Hardware Acceleration setting is configured, Hardware ({hw_names}) is encoder-ready. "
                f"Will use software fallback if/when needed."
            )
        else:
            label = _hardware_provider_label(gpu_capabilities, provider_name)
            reason = "Configured to always use software fallback (libx264)."
    elif detected_devices:
        label = _hardware_provider_label(gpu_capabilities, provider_name)
        reason = (
            "Hardware was detected but could not be validated for encoding; "
            "software fallback is active. "
            "See the GPU Providers section below for details."
        )
    else:
        label = _hardware_provider_label(gpu_capabilities, provider_name)
        reason = "No hardware encoder was detected or validated; software fallback is active."
    return {
        "provider": provider_name,
        "label": label,
        "codec": profile.video_codec,
        "using_hardware": using_hardware,
        "reason": reason,
    }


def _hardware_provider_label(gpu_capabilities: dict | None, provider_name: str) -> str:
    if provider_name == "software":
        return "Software fallback (libx264)"
    if isinstance(gpu_capabilities, dict):
        providers = gpu_capabilities.get("providers")
        if isinstance(providers, dict):
            provider = providers.get(provider_name)
            if isinstance(provider, dict):
                label = provider.get("label")
                if label:
                    return str(label)
    return provider_name


# ─────────────────────────────────────────────────────────────────────────────
# WeatherChannelManager
# ─────────────────────────────────────────────────────────────────────────────

WEATHER_STATE_PATH          = DATA_DIR / "weather_state.json"
WEATHER_RENDERER_PID_FILE   = DATA_DIR / "weather_renderer.pid"
WEATHER_FFMPEG_PID_FILE     = DATA_DIR / "weather_ffmpeg.pid"
WEATHER_PLAYLIST            = OUTPUT_DIR / "weather.m3u8"
WEATHER_SEGMENT_PREFIX      = "weather_"
# Weather data is refetched on this cadence (seconds) regardless of
# seconds_per_segment so the ticker and "last updated" stamp stay fresh.
WEATHER_FETCH_INTERVAL_SECS = 120


def _resolve_video_encoder_path(profile: FFmpegProfile) -> tuple[str, list[str], Optional[str], Optional[str], str, str]:
    """Return codec, codec-specific args, preset, tune, path label, and pixel format.

    Hardware encoders such as Intel QSV (h264_qsv) and AMD AMF (h264_amf) require
    the ``nv12`` pixel format rather than ``yuv420p``.  Returning the required
    pixel format here lets the command builders select the correct ``-pix_fmt``
    argument without hard-coding ``yuv420p`` everywhere.
    """
    codec = (profile.video_codec or "").strip().lower()
    encoder_map: dict[str, tuple[list[str], Optional[str], Optional[str], str, str]] = {
        "h264_nvenc": (["-rc", "vbr", "-cq", "23", "-forced-idr", "1"], None, None, "hardware:nvidia", "yuv420p"),
        "h264_qsv":   (["-look_ahead", "0", "-global_quality", "23"],   None, None, "hardware:intel",  "nv12"),
        "h264_amf":   (["-quality", "balanced"],                         None, None, "hardware:amd",    "nv12"),
        "h264_vaapi": (["-qp", "23"],                                    None, None, "hardware:vaapi",  ""),
        "libx264":    ([],                                 profile.preset, profile.tune, "software:libx264", "yuv420p"),
    }
    selected = encoder_map.get(codec)
    if selected:
        codec_args, preset, tune, path_label, pix_fmt = selected
        return codec, codec_args, preset, tune, path_label, pix_fmt
    if profile.encoder_type != "hardware":
        return codec or "libx264", [], profile.preset, profile.tune, f"software:custom-{codec or 'unknown'}", "yuv420p"
    return "libx264", [], "veryfast", "zerolatency", f"software:fallback-from-{codec or 'unknown'}", "yuv420p"


def _resolve_hw_device_init_args(codec: str) -> list[str]:
    """Return global ffmpeg device initialisation arguments for hardware encoders.

    These *must* be placed **before** the first ``-i`` (input) in the ffmpeg
    command so that the hardware context is available when the codec is opened.

    Intel QSV (``h264_qsv``, ``hevc_qsv``, ``av1_qsv``) requires an explicit
    ``-init_hw_device qsv=hw`` declaration.  Without it, ffmpeg may fail to
    create a QSV context on many Intel systems (particularly Docker containers
    where the device is accessible via ``/dev/dri`` but the auto-detection path
    in libmfx/oneVPL is not reliable).
    """
    qsv_codecs = {"h264_qsv", "hevc_qsv", "av1_qsv"}
    if codec in qsv_codecs:
        return ["-init_hw_device", "qsv=hw"]
    vaapi_codecs = {"h264_vaapi", "hevc_vaapi", "av1_vaapi"}
    if codec in vaapi_codecs:
        # VA-API encoders require an explicit render node.  Detection can
        # validate the encoder without the live pipeline automatically creating
        # a usable hardware frames context, so initialise it here as well.
        return ["-vaapi_device", os.environ.get("RSMC_VAAPI_DEVICE", "/dev/dri/renderD128")]
    return []


def _resolve_hw_video_filter_args(codec: str) -> list[str]:
    """Return filters needed to upload software-rendered frames to hardware.

    RSMC renderers write RGB24 frames through stdout. VA-API cannot consume
    those system-memory frames directly: they must first be converted to NV12
    and uploaded to a VA-API hardware surface.
    """
    if codec in {"h264_vaapi", "hevc_vaapi", "av1_vaapi"}:
        return ["-vf", "format=nv12,hwupload"]
    return []


def _apply_hw_filter_to_guide(
    codec: str,
    video_filter_args: list[str],
    video_map_args: list[str],
) -> tuple[list[str], list[str]]:
    """Add the VA-API upload stage without breaking Guide preview filters."""
    if codec not in {"h264_vaapi", "hevc_vaapi", "av1_vaapi"}:
        return video_filter_args, video_map_args

    args = list(video_filter_args)
    maps = list(video_map_args)
    if "-filter_complex" in args:
        idx = args.index("-filter_complex")
        if idx + 1 < len(args):
            graph = args[idx + 1]
            # The Guide preview graph terminates in [vout]. Add the hardware
            # upload as a final stage and map the uploaded surface instead.
            if "[vout]" in graph:
                graph += ";[vout]format=nv12,hwupload[vout_hw]"
                args[idx + 1] = graph
                maps = ["[vout_hw]" if item == "[vout]" else item for item in maps]
                return args, maps
    # No preview/filter_complex path: apply a normal single-video filter.
    args.extend(_resolve_hw_video_filter_args(codec))
    return args, maps


def _build_weather_ffmpeg_command(
    profile: FFmpegProfile,
    fps: str,
    start_number: str,
    config: dict | None = None,
) -> list[str]:
    """Return the ffmpeg command that encodes weather renderer frames to HLS."""
    try:
        width, height = [int(v) for v in profile.resolution.lower().split("x", 1)]
    except ValueError:
        width, height = 1280, 720
    gop = int(fps) * HLS_KEYFRAME_INTERVAL_SECS
    video_codec, video_codec_args, preset, tune, _, pix_fmt = _resolve_video_encoder_path(profile)
    hw_device_init_args = _resolve_hw_device_init_args(video_codec)
    audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(
        config or {},
        profile.audio_codec,
        key_prefix="weather_music_",
        music_dir=MUSIC_DIR,
        playlist_filename="weather_music_playlist.txt",
    )
    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error", "-y",
        *hw_device_init_args,
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}",
        "-r", fps,
        "-i", "-",
        *audio_input_args,
        *_resolve_hw_video_filter_args(video_codec),
        "-c:v", video_codec,
    ]
    ffmpeg_cmd.extend(video_codec_args)
    if preset:
        ffmpeg_cmd.extend(["-preset", preset])
    if tune:
        ffmpeg_cmd.extend(["-tune", tune])
    ffmpeg_cmd.extend([
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECS})",
        "-sc_threshold", "0",
    ])
    if profile.bitrate:
        ffmpeg_cmd.extend(["-b:v", profile.bitrate, "-maxrate", profile.bitrate])
        try:
            if str(profile.bitrate).lower().endswith("k"):
                bufsize = f"{int(str(profile.bitrate)[:-1]) * 2}k"
            elif str(profile.bitrate).lower().endswith("m"):
                bufsize = f"{float(str(profile.bitrate)[:-1]) * 2:g}m"
            else:
                bufsize = str(int(profile.bitrate) * 2)
            ffmpeg_cmd.extend(["-bufsize", bufsize])
        except (TypeError, ValueError):
            pass
    if pix_fmt:
        ffmpeg_cmd.extend(["-pix_fmt", pix_fmt])
    ffmpeg_cmd.extend([
        *audio_codec_args,
        *audio_map_args,
        "-f", "hls",
        "-hls_time", "6",
        "-hls_segment_type", "mpegts",
        "-hls_list_size", "10",
        "-segment_list_flags", "+live",
        "-hls_flags",
        "delete_segments+program_date_time+omit_endlist+discont_start+independent_segments",
        "-start_number", start_number,
        "-hls_segment_filename", str(OUTPUT_DIR / "weather_%d.ts"),
        str(WEATHER_PLAYLIST),
    ])
    return ffmpeg_cmd


class WeatherChannelManager:
    """Manages the live HLS pipeline for the weather virtual channel.

    A background worker thread:
    1. Calls *data_fetcher* periodically to obtain current weather data and
       writes it to :data:`WEATHER_STATE_PATH` so the renderer can read it.
    2. Ensures the ``weather_renderer.py`` + ffmpeg sub-processes are running
       whenever ``weather_channel_enabled`` is *True* in the config store.
    3. Auto-restarts the pipeline if either process exits unexpectedly.

    The pipeline outputs ``output/weather.m3u8`` + ``output/weather_*.ts``.
    These files are intentionally excluded from
    :meth:`GuideManager._clean_output_dir` so guide restarts don't interrupt
    the weather stream.
    """

    def __init__(self, store: ConfigStore, data_fetcher) -> None:
        """
        Parameters
        ----------
        store:
            Shared config store (same instance used by :class:`GuideManager`).
        data_fetcher:
            Zero-argument callable that returns a weather data ``dict`` (the
            same shape as ``/api/weather``) or *None* on failure.
        """
        self.store        = store
        self.logger       = AppLogger(store)
        self._data_fetcher = data_fetcher

        self._lock: threading.Lock       = threading.Lock()
        self._renderer_pid:   Optional[int]              = None
        self._ffmpeg_pid:     Optional[int]              = None
        self._renderer_popen: Optional[subprocess.Popen] = None
        self._ffmpeg_popen:   Optional[subprocess.Popen] = None
        self._pipeline_active: bool      = False

        self._stop_event    = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._last_fetch_at: float = 0.0
        # Hardware-encoder runtime fallback state (mirrors GuideManager).
        self._hw_failure_count: int = 0
        self._hw_fallback_forced: bool = False
        self._last_encoder_type: str = "software"
        self._pipeline_started_at: float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker (idempotent)."""
        self._stop_event.clear()
        if self._worker_thread and self._worker_thread.is_alive():
            return

        # Try to reattach to a surviving pipeline from a prior Flask run.
        renderer_pid = _load_pid(WEATHER_RENDERER_PID_FILE)
        ffmpeg_pid   = _load_pid(WEATHER_FFMPEG_PID_FILE)
        if (
            renderer_pid and _pid_alive(renderer_pid) and _pid_matches(renderer_pid, "weather_renderer")
            and ffmpeg_pid and _pid_alive(ffmpeg_pid) and _pid_matches(ffmpeg_pid, "ffmpeg")
        ):
            cfg = self.store.get_config()
            if cfg.get("weather_channel_enabled"):
                self._renderer_pid    = renderer_pid
                self._ffmpeg_pid      = ffmpeg_pid
                self._pipeline_active = True
                self.logger.info(
                    "weather",
                    f"Reattached to existing weather pipeline "
                    f"(renderer PID {renderer_pid}, ffmpeg PID {ffmpeg_pid})",
                )

        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="weather-worker"
        )
        self._worker_thread.start()
        self.logger.info("weather", "Weather channel manager started")

    def stop(self) -> None:
        """Stop the background worker and terminate the pipeline."""
        self._stop_event.set()
        self._stop_pipeline()
        self.logger.info("weather", "Weather channel manager stopped")

    # ── Background worker ─────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                cfg     = self.store.get_config()
                enabled = bool(cfg.get("weather_channel_enabled", False))

                if enabled:
                    self._maybe_fetch_state()
                    self._ensure_pipeline_running()
                else:
                    if self._pipeline_active:
                        self._stop_pipeline()
                        self.logger.info("weather", "Weather channel disabled; pipeline stopped")
            except Exception as exc:
                self.logger.error(
                    "weather",
                    f"Worker error ({exc.__class__.__name__}): {exc}",
                )
            self._stop_event.wait(30)

    # ── Weather state ─────────────────────────────────────────────────────────

    def _maybe_fetch_state(self) -> None:
        now = time.time()
        if now - self._last_fetch_at < WEATHER_FETCH_INTERVAL_SECS:
            return
        try:
            data = self._data_fetcher()
            if data is not None:
                # Include seconds_per_segment so the renderer can determine
                # segment timing without reading the full config store.
                cfg = self.store.get_config()
                try:
                    sps = max(30, min(600, int(cfg.get("weather_seconds_per_segment", 300) or 300)))
                except (TypeError, ValueError):
                    sps = 300
                data["seconds_per_segment"] = sps
                data["units"] = cfg.get("weather_units", "F")
                data["timezone"] = cfg.get("timezone", "local")
                data["browser_timezone"] = (cfg.get("browser_timezone") or "").strip()
                WEATHER_STATE_PATH.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
                self._last_fetch_at = now
                self.logger.info("weather", "Weather state updated")
        except Exception as exc:
            self.logger.error("weather", f"Failed to fetch/save weather state: {exc}")

    # ── Pipeline management ───────────────────────────────────────────────────

    def start_pipeline(self) -> None:
        """Start (or restart) the weather renderer + ffmpeg pipeline."""
        # Force a fresh state fetch before starting the renderer.
        self._last_fetch_at = 0.0
        self._maybe_fetch_state()

        with self._lock:
            self._stop_pipeline_locked()
            self._clean_weather_output()

            cfg        = self.store.get_config()
            if self._hw_fallback_forced:
                cfg = {**cfg, "hardware_acceleration_mode": "software_fallback"}
                self.logger.warning(
                    "weather",
                    f"Hardware encoder failed {HW_ENCODER_MAX_CONSECUTIVE_FAILURES} consecutive time(s) quickly; "
                    "forcing software fallback (libx264) for the remainder of this session.",
                )
            try:
                gpu_capabilities = detect_gpu_capabilities()
            except Exception as exc:
                self.logger.warning("weather", f"GPU detection unavailable; using software fallback profile: {exc}")
                gpu_capabilities = {}
            profile    = resolve_ffmpeg_profile(cfg, gpu_capabilities)
            self._last_encoder_type  = profile.encoder_type
            selected_codec, _, _, _, encoder_path, _ = _resolve_video_encoder_path(profile)
            self.logger.info(
                "weather",
                f"Selected encoder path: {encoder_path} (codec={selected_codec}, provider={profile.hardware_acceleration_provider or 'software'})",
            )
            resolution = cfg.get("weather_resolution") or profile.resolution
            fps        = str(cfg.get("fps", 10))
            start_num  = str(int(time.time()) // 6)

            # Apply the weather-specific resolution to the profile so that
            # _build_weather_ffmpeg_command uses the right frame dimensions.
            if resolution != profile.resolution:
                profile = replace(profile, resolution=resolution)

            renderer_cmd = [
                sys.executable,
                str(BASE_DIR / "app" / "weather_renderer.py"),
                "--state",      str(WEATHER_STATE_PATH),
                "--fps",        fps,
                "--resolution", resolution,
            ]
            ffmpeg_cmd = _build_weather_ffmpeg_command(profile, fps, start_num, cfg)

            try:
                renderer_popen = subprocess.Popen(
                    renderer_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                self.logger.error("weather", f"Failed to start weather renderer: {exc}")
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
                _terminate_pid(renderer_popen.pid, renderer_popen, self.logger, "weather-renderer")
                self.logger.error("weather", f"Failed to start weather ffmpeg: {exc}")
                return

            renderer_popen.stdout.close()
            renderer_popen.stdout = None

            self._renderer_popen = renderer_popen
            self._ffmpeg_popen   = ffmpeg_popen
            self._renderer_pid   = renderer_popen.pid
            self._ffmpeg_pid     = ffmpeg_popen.pid
            self._pipeline_started_at = time.time()
            self._pipeline_active = True

            _save_pid(WEATHER_RENDERER_PID_FILE, renderer_popen.pid)
            _save_pid(WEATHER_FFMPEG_PID_FILE, ffmpeg_popen.pid)
            self.logger.info(
                "weather",
                f"Weather pipeline started "
                f"(renderer PID {renderer_popen.pid}, ffmpeg PID {ffmpeg_popen.pid})",
            )

        _start_stderr_reader(renderer_popen, "weather.renderer", self.logger)
        _start_stderr_reader(ffmpeg_popen,   "weather.ffmpeg",   self.logger)

    def _stop_pipeline_locked(self) -> None:
        """Terminate weather processes.  Caller must hold *_lock*."""
        for pid, popen, label in [
            (self._ffmpeg_pid,   self._ffmpeg_popen,   "weather-ffmpeg"),
            (self._renderer_pid, self._renderer_popen, "weather-renderer"),
        ]:
            if pid is None:
                continue
            if popen is not None and popen.poll() is not None:
                try:
                    popen.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            elif _pid_alive(pid):
                _terminate_pid(pid, popen, self.logger, label)

        WEATHER_RENDERER_PID_FILE.unlink(missing_ok=True)
        WEATHER_FFMPEG_PID_FILE.unlink(missing_ok=True)

        self._renderer_pid    = None
        self._ffmpeg_pid      = None
        self._renderer_popen  = None
        self._ffmpeg_popen    = None
        self._pipeline_active = False

    def _stop_pipeline(self) -> None:
        with self._lock:
            self._stop_pipeline_locked()

    def _clean_weather_output(self) -> None:
        """Remove stale weather.m3u8 and weather_*.ts from a prior run."""
        for path in OUTPUT_DIR.iterdir():
            if path.name == "weather.m3u8" or path.name.startswith(WEATHER_SEGMENT_PREFIX):
                try:
                    path.unlink()
                except OSError as exc:
                    self.logger.warning("weather", f"Could not remove {path}: {exc}")

    def _ensure_pipeline_running(self) -> None:
        if not self._pipeline_active:
            self.start_pipeline()
            return

        if self._renderer_popen is not None:
            renderer_dead = self._renderer_popen.poll() is not None
        else:
            renderer_dead = self._renderer_pid is None or not _pid_alive(self._renderer_pid)

        if self._ffmpeg_popen is not None:
            ffmpeg_dead = self._ffmpeg_popen.poll() is not None
        else:
            ffmpeg_dead = self._ffmpeg_pid is None or not _pid_alive(self._ffmpeg_pid)

        if renderer_dead or ffmpeg_dead:
            parts = []
            if renderer_dead:
                parts.append(f"weather-renderer (PID {self._renderer_pid})")
            if ffmpeg_dead:
                parts.append(f"weather-ffmpeg (PID {self._ffmpeg_pid})")
            self.logger.warning(
                "weather", f"Dead process(es): {', '.join(parts)} — restarting weather pipeline"
            )

            elapsed = time.time() - self._pipeline_started_at
            weather_had_output = playlist_path_has_segments(WEATHER_PLAYLIST, WEATHER_SEGMENT_PREFIX)
            if self._last_encoder_type == "hardware" and (
                elapsed < HW_ENCODER_QUICK_FAILURE_WINDOW_SECS or not weather_had_output
            ):
                self._hw_failure_count += 1
                if self._hw_failure_count >= HW_ENCODER_MAX_CONSECUTIVE_FAILURES and not self._hw_fallback_forced:
                    self._hw_fallback_forced = True
                    self.logger.warning(
                        "weather",
                        f"Hardware encoder has failed {self._hw_failure_count} time(s) before establishing stable HLS output; "
                        "switching to software (libx264) fallback.",
                    )
            else:
                self._hw_failure_count = 0

            self.start_pipeline()

    # ── Status helpers ────────────────────────────────────────────────────────

    def is_weather_buffered(self, min_segments: int = 3) -> bool:
        """Return True once the weather stream has at least *min_segments* ready."""
        try:
            text = WEATHER_PLAYLIST.read_text(encoding="utf-8")
            count = sum(
                1 for line in text.splitlines()
                if line.strip().endswith(".ts") and line.strip().startswith(WEATHER_SEGMENT_PREFIX)
            )
            return count >= min_segments
        except OSError:
            return False

    def status(self) -> dict:
        return {
            "pipeline_active":  self._pipeline_active,
            "renderer_running": self._renderer_pid is not None and _pid_alive(self._renderer_pid),
            "ffmpeg_running":   self._ffmpeg_pid   is not None and _pid_alive(self._ffmpeg_pid),
            "buffered":         self.is_weather_buffered(),
        }


# ── TrafficChannelManager ────────────────────────────────────────────────────

TRAFFIC_STATE_PATH        = DATA_DIR / "traffic_state.json"
TRAFFIC_RENDERER_PID_FILE = DATA_DIR / "traffic_renderer.pid"
TRAFFIC_FFMPEG_PID_FILE   = DATA_DIR / "traffic_ffmpeg.pid"
TRAFFIC_PLAYLIST          = OUTPUT_DIR / "traffic.m3u8"
TRAFFIC_SEGMENT_PREFIX    = "traffic_"
TRAFFIC_FETCH_INTERVAL_SECS = 10


def _build_traffic_ffmpeg_command(
    profile: FFmpegProfile,
    fps: str,
    start_number: str,
    config: dict | None = None,
) -> list[str]:
    """Encode TrafficRenderer RGB24 frames into a standalone HLS channel."""
    try:
        width, height = [int(v) for v in profile.resolution.lower().split("x", 1)]
    except ValueError:
        width, height = 1280, 720
    gop = int(fps) * HLS_KEYFRAME_INTERVAL_SECS
    video_codec, video_codec_args, preset, tune, _, pix_fmt = _resolve_video_encoder_path(profile)
    hw_device_init_args = _resolve_hw_device_init_args(video_codec)
    audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(
        config or {}, profile.audio_codec, key_prefix="traffic_music_", music_dir=MUSIC_DIR,
        playlist_filename="traffic_music_playlist.txt",
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *hw_device_init_args,
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", fps, "-i", "-",
        *audio_input_args,
        *_resolve_hw_video_filter_args(video_codec),
        "-c:v", video_codec,
    ]
    cmd.extend(video_codec_args)
    if preset:
        cmd.extend(["-preset", preset])
    if tune:
        cmd.extend(["-tune", tune])
    cmd.extend([
        "-g", str(gop), "-keyint_min", str(gop),
        "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECS})",
        "-sc_threshold", "0",
    ])
    if profile.bitrate:
        cmd.extend(["-b:v", profile.bitrate])
    if pix_fmt:
        cmd.extend(["-pix_fmt", pix_fmt])
    cmd.extend([
        *audio_codec_args, *audio_map_args,
        "-f", "hls", "-hls_time", "6", "-hls_segment_type", "mpegts",
        "-hls_list_size", "10", "-segment_list_flags", "+live",
        "-hls_flags", "delete_segments+program_date_time+omit_endlist+discont_start+independent_segments",
        "-start_number", start_number,
        "-hls_segment_filename", str(OUTPUT_DIR / "traffic_%d.ts"),
        str(TRAFFIC_PLAYLIST),
    ])
    return cmd


class TrafficChannelManager:
    """Manages the rendered HLS pipeline for the simulated Traffic channel."""

    def __init__(self, store: ConfigStore, data_fetcher) -> None:
        self.store = store
        self.logger = AppLogger(store)
        self._data_fetcher = data_fetcher
        self._lock = threading.Lock()
        self._renderer_pid: Optional[int] = None
        self._ffmpeg_pid: Optional[int] = None
        self._renderer_popen: Optional[subprocess.Popen] = None
        self._ffmpeg_popen: Optional[subprocess.Popen] = None
        self._pipeline_active = False
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._last_fetch_at = 0.0
        self._hw_failure_count = 0
        self._hw_fallback_forced = False
        self._last_encoder_type = "software"
        self._pipeline_started_at = 0.0

    def start(self) -> None:
        self._stop_event.clear()
        if self._worker_thread and self._worker_thread.is_alive():
            return
        renderer_pid = _load_pid(TRAFFIC_RENDERER_PID_FILE)
        ffmpeg_pid = _load_pid(TRAFFIC_FFMPEG_PID_FILE)
        if (
            renderer_pid and _pid_alive(renderer_pid) and _pid_matches(renderer_pid, "traffic_renderer")
            and ffmpeg_pid and _pid_alive(ffmpeg_pid) and _pid_matches(ffmpeg_pid, "ffmpeg")
            and self.store.get_config().get("traffic_channel_enabled")
        ):
            self._renderer_pid = renderer_pid
            self._ffmpeg_pid = ffmpeg_pid
            self._pipeline_active = True
            self.logger.info("traffic", f"Reattached to existing traffic pipeline (renderer PID {renderer_pid}, ffmpeg PID {ffmpeg_pid})")
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="traffic-worker")
        self._worker_thread.start()
        self.logger.info("traffic", "Traffic channel manager started")

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_pipeline()
        self.logger.info("traffic", "Traffic channel manager stopped")

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                enabled = bool(self.store.get_config().get("traffic_channel_enabled", False))
                if enabled:
                    self._maybe_fetch_state()
                    self._ensure_pipeline_running()
                elif self._pipeline_active:
                    self._stop_pipeline()
                    self.logger.info("traffic", "Traffic channel disabled; pipeline stopped")
            except Exception as exc:
                self.logger.error("traffic", f"Worker error ({exc.__class__.__name__}): {exc}")
            self._stop_event.wait(10)

    def _maybe_fetch_state(self) -> None:
        now = time.time()
        if now - self._last_fetch_at < TRAFFIC_FETCH_INTERVAL_SECS:
            return
        try:
            data = self._data_fetcher()
            if data is not None:
                cfg = self.store.get_config()
                data["timezone"] = (cfg.get("timezone") or "local").strip()
                data["browser_timezone"] = (cfg.get("browser_timezone") or "").strip()
                TRAFFIC_STATE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self._last_fetch_at = now
        except Exception as exc:
            self.logger.error("traffic", f"Failed to fetch/save traffic state: {exc}")

    def start_pipeline(self) -> None:
        self._last_fetch_at = 0.0
        self._maybe_fetch_state()
        with self._lock:
            self._stop_pipeline_locked()
            self._clean_traffic_output()
            cfg = self.store.get_config()
            if self._hw_fallback_forced:
                cfg = {**cfg, "hardware_acceleration_mode": "software_fallback"}
            try:
                gpu_capabilities = detect_gpu_capabilities()
            except Exception as exc:
                self.logger.warning("traffic", f"GPU detection unavailable; using software fallback profile: {exc}")
                gpu_capabilities = {}
            profile = resolve_ffmpeg_profile(cfg, gpu_capabilities)
            self._last_encoder_type = profile.encoder_type
            resolution = str(cfg.get("traffic_resolution") or profile.resolution or "1280x720")
            fps = str(cfg.get("fps", 10))
            start_num = str(int(time.time()) // 6)
            if resolution != profile.resolution:
                profile = replace(profile, resolution=resolution)
            renderer_cmd = [
                sys.executable, str(BASE_DIR / "app" / "traffic_renderer.py"),
                "--state", str(TRAFFIC_STATE_PATH), "--fps", fps, "--resolution", resolution,
            ]
            ffmpeg_cmd = _build_traffic_ffmpeg_command(profile, fps, start_num, cfg)
            try:
                renderer = subprocess.Popen(renderer_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            except OSError as exc:
                self.logger.error("traffic", f"Failed to start traffic renderer: {exc}")
                return
            try:
                ffmpeg = subprocess.Popen(ffmpeg_cmd, stdin=renderer.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, start_new_session=True)
            except OSError as exc:
                _terminate_pid(renderer.pid, renderer, self.logger, "traffic-renderer")
                self.logger.error("traffic", f"Failed to start traffic ffmpeg: {exc}")
                return
            renderer.stdout.close()
            renderer.stdout = None
            self._renderer_popen, self._ffmpeg_popen = renderer, ffmpeg
            self._renderer_pid, self._ffmpeg_pid = renderer.pid, ffmpeg.pid
            self._pipeline_started_at = time.time()
            self._pipeline_active = True
            _save_pid(TRAFFIC_RENDERER_PID_FILE, renderer.pid)
            _save_pid(TRAFFIC_FFMPEG_PID_FILE, ffmpeg.pid)
            self.logger.info("traffic", f"Traffic pipeline started (renderer PID {renderer.pid}, ffmpeg PID {ffmpeg.pid})")
        _start_stderr_reader(renderer, "traffic.renderer", self.logger)
        _start_stderr_reader(ffmpeg, "traffic.ffmpeg", self.logger)

    def _stop_pipeline_locked(self) -> None:
        for pid, popen, label in [
            (self._ffmpeg_pid, self._ffmpeg_popen, "traffic-ffmpeg"),
            (self._renderer_pid, self._renderer_popen, "traffic-renderer"),
        ]:
            if pid is None:
                continue
            if popen is not None and popen.poll() is not None:
                try:
                    popen.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            elif _pid_alive(pid):
                _terminate_pid(pid, popen, self.logger, label)
        TRAFFIC_RENDERER_PID_FILE.unlink(missing_ok=True)
        TRAFFIC_FFMPEG_PID_FILE.unlink(missing_ok=True)
        self._renderer_pid = self._ffmpeg_pid = None
        self._renderer_popen = self._ffmpeg_popen = None
        self._pipeline_active = False

    def _stop_pipeline(self) -> None:
        with self._lock:
            self._stop_pipeline_locked()

    def _clean_traffic_output(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for path in OUTPUT_DIR.iterdir():
            if path.name == "traffic.m3u8" or path.name.startswith(TRAFFIC_SEGMENT_PREFIX):
                try:
                    path.unlink()
                except OSError as exc:
                    self.logger.warning("traffic", f"Could not remove {path}: {exc}")

    def _ensure_pipeline_running(self) -> None:
        if not self._pipeline_active:
            self.start_pipeline()
            return
        renderer_dead = self._renderer_popen.poll() is not None if self._renderer_popen is not None else self._renderer_pid is None or not _pid_alive(self._renderer_pid)
        ffmpeg_dead = self._ffmpeg_popen.poll() is not None if self._ffmpeg_popen is not None else self._ffmpeg_pid is None or not _pid_alive(self._ffmpeg_pid)
        if renderer_dead or ffmpeg_dead:
            elapsed = time.time() - self._pipeline_started_at
            traffic_had_output = playlist_path_has_segments(TRAFFIC_PLAYLIST, TRAFFIC_SEGMENT_PREFIX)
            if self._last_encoder_type == "hardware" and (
                elapsed < HW_ENCODER_QUICK_FAILURE_WINDOW_SECS or not traffic_had_output
            ):
                self._hw_failure_count += 1
                if self._hw_failure_count >= HW_ENCODER_MAX_CONSECUTIVE_FAILURES:
                    self._hw_fallback_forced = True
                    self.logger.warning(
                        "traffic",
                        f"Hardware encoder has failed {self._hw_failure_count} time(s) before producing HLS output; "
                        "switching to software (libx264) fallback.",
                    )
            else:
                self._hw_failure_count = 0
            self.logger.warning("traffic", "Traffic pipeline process exited; restarting")
            self.start_pipeline()

    def is_traffic_buffered(self, min_segments: int = 3) -> bool:
        try:
            text = TRAFFIC_PLAYLIST.read_text(encoding="utf-8")
            return sum(1 for line in text.splitlines() if line.strip().startswith(TRAFFIC_SEGMENT_PREFIX) and line.strip().endswith(".ts")) >= min_segments
        except OSError:
            return False

    def status(self) -> dict:
        return {
            "pipeline_active": self._pipeline_active,
            "renderer_running": self._renderer_pid is not None and _pid_alive(self._renderer_pid),
            "ffmpeg_running": self._ffmpeg_pid is not None and _pid_alive(self._ffmpeg_pid),
            "buffered": self.is_traffic_buffered(),
        }


# ── NewsChannelManager ───────────────────────────────────────────────────────
NEWS_STATE_PATH = DATA_DIR / "news_state.json"
NEWS_RENDERER_PID_FILE = DATA_DIR / "news_renderer.pid"
NEWS_FFMPEG_PID_FILE = DATA_DIR / "news_ffmpeg.pid"
NEWS_PLAYLIST = OUTPUT_DIR / "news.m3u8"
NEWS_SEGMENT_PREFIX = "news_"
NEWS_FETCH_INTERVAL_SECS = 15


def _build_news_ffmpeg_command(profile: FFmpegProfile, fps: str, start_number: str, config: dict | None = None) -> list[str]:
    try:
        width, height = [int(v) for v in profile.resolution.lower().split("x", 1)]
    except ValueError:
        width, height = 1280, 720
    gop = int(float(fps)) * HLS_KEYFRAME_INTERVAL_SECS
    video_codec, video_codec_args, preset, tune, _, pix_fmt = _resolve_video_encoder_path(profile)
    audio_input_args, audio_codec_args, audio_map_args = _build_audio_ffmpeg_args(
        config or {}, profile.audio_codec, key_prefix="news_music_", music_dir=MUSIC_DIR,
        playlist_filename="news_music_playlist.txt",
    )
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *_resolve_hw_device_init_args(video_codec),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", fps, "-i", "-",
           *audio_input_args, *_resolve_hw_video_filter_args(video_codec), "-c:v", video_codec]
    cmd.extend(video_codec_args)
    if preset: cmd.extend(["-preset", preset])
    if tune: cmd.extend(["-tune", tune])
    cmd.extend(["-g", str(gop), "-keyint_min", str(gop), "-force_key_frames", f"expr:gte(t,n_forced*{HLS_KEYFRAME_INTERVAL_SECS})", "-sc_threshold", "0"])
    if profile.bitrate: cmd.extend(["-b:v", profile.bitrate])
    if pix_fmt: cmd.extend(["-pix_fmt", pix_fmt])
    cmd.extend([*audio_codec_args, *audio_map_args, "-f", "hls", "-hls_time", "6", "-hls_segment_type", "mpegts", "-hls_list_size", "10",
                "-segment_list_flags", "+live", "-hls_flags", "delete_segments+program_date_time+omit_endlist+discont_start+independent_segments",
                "-start_number", start_number, "-hls_segment_filename", str(OUTPUT_DIR / "news_%d.ts"), str(NEWS_PLAYLIST)])
    return cmd


class NewsChannelManager:
    """Manages the generated RSMC News Now HLS pipeline."""
    def __init__(self, store: ConfigStore, data_fetcher) -> None:
        self.store=store; self.logger=AppLogger(store); self._data_fetcher=data_fetcher; self._lock=threading.Lock()
        self._renderer_pid=None; self._ffmpeg_pid=None; self._renderer_popen=None; self._ffmpeg_popen=None; self._pipeline_active=False
        self._stop_event=threading.Event(); self._worker_thread=None; self._last_fetch_at=0.0; self._hw_failure_count=0; self._hw_fallback_forced=False; self._last_encoder_type='software'; self._pipeline_started_at=0.0
    def start(self):
        self._stop_event.clear()
        if self._worker_thread and self._worker_thread.is_alive(): return
        self._worker_thread=threading.Thread(target=self._worker_loop,daemon=True,name='news-worker'); self._worker_thread.start(); self.logger.info('news','News channel manager started')
    def stop(self): self._stop_event.set(); self._stop_pipeline(); self.logger.info('news','News channel manager stopped')
    def _worker_loop(self):
        while not self._stop_event.is_set():
            try:
                if self.store.get_config().get('news_channel_enabled'):
                    self._maybe_fetch_state(); self._ensure_pipeline_running()
                elif self._pipeline_active: self._stop_pipeline()
            except Exception as exc: self.logger.error('news',f'Worker error ({exc.__class__.__name__}): {exc}')
            self._stop_event.wait(10)
    def _maybe_fetch_state(self):
        now=time.time()
        if now-self._last_fetch_at < NEWS_FETCH_INTERVAL_SECS: return
        try:
            data=self._data_fetcher(); cfg=self.store.get_config(); data['timezone']=(cfg.get('timezone') or 'local').strip(); data['browser_timezone']=(cfg.get('browser_timezone') or '').strip(); NEWS_STATE_PATH.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8'); self._last_fetch_at=now
        except Exception as exc: self.logger.error('news',f'Failed to fetch/save news state: {exc}')
    def start_pipeline(self):
        self._last_fetch_at=0; self._maybe_fetch_state()
        with self._lock:
            self._stop_pipeline_locked(); self._clean_output(); cfg=self.store.get_config()
            if self._hw_fallback_forced:
                cfg={**cfg,'hardware_acceleration_mode':'software_fallback'}
                self.logger.warning('news','Hardware encoder fallback active; using software (libx264) for this session.')
            try: caps=detect_gpu_capabilities()
            except Exception: caps={}
            profile=resolve_ffmpeg_profile(cfg,caps); self._last_encoder_type=profile.encoder_type; resolution=str(cfg.get('news_resolution') or profile.resolution or '1280x720'); fps=str(cfg.get('fps',15)); start_num=str(int(time.time())//6)
            if resolution != profile.resolution: profile=replace(profile,resolution=resolution)
            renderer_cmd=[sys.executable,str(BASE_DIR/'app'/'news_renderer.py'),'--state',str(NEWS_STATE_PATH),'--fps',fps,'--resolution',resolution]
            try: renderer=subprocess.Popen(renderer_cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            except OSError as exc: self.logger.error('news',f'Failed to start news renderer: {exc}'); return
            try: ffmpeg=subprocess.Popen(_build_news_ffmpeg_command(profile,fps,start_num,cfg),stdin=renderer.stdout,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,start_new_session=True)
            except OSError as exc: _terminate_pid(renderer.pid,renderer,self.logger,'news-renderer'); self.logger.error('news',f'Failed to start news ffmpeg: {exc}'); return
            renderer.stdout.close(); renderer.stdout=None; self._renderer_popen=renderer; self._ffmpeg_popen=ffmpeg; self._renderer_pid=renderer.pid; self._ffmpeg_pid=ffmpeg.pid; self._pipeline_started_at=time.time(); self._pipeline_active=True
            _save_pid(NEWS_RENDERER_PID_FILE,renderer.pid); _save_pid(NEWS_FFMPEG_PID_FILE,ffmpeg.pid); self.logger.info('news',f'News pipeline started (renderer PID {renderer.pid}, ffmpeg PID {ffmpeg.pid})')
        _start_stderr_reader(renderer,'news.renderer',self.logger); _start_stderr_reader(ffmpeg,'news.ffmpeg',self.logger)
    def _stop_pipeline_locked(self):
        for pid,popen,label in [(self._ffmpeg_pid,self._ffmpeg_popen,'news-ffmpeg'),(self._renderer_pid,self._renderer_popen,'news-renderer')]:
            if not pid: continue
            if popen is not None and popen.poll() is not None:
                try: popen.wait(timeout=1)
                except subprocess.TimeoutExpired: pass
            elif _pid_alive(pid): _terminate_pid(pid,popen,self.logger,label)
        NEWS_RENDERER_PID_FILE.unlink(missing_ok=True); NEWS_FFMPEG_PID_FILE.unlink(missing_ok=True); self._renderer_pid=self._ffmpeg_pid=None; self._renderer_popen=self._ffmpeg_popen=None; self._pipeline_active=False
    def _stop_pipeline(self):
        with self._lock: self._stop_pipeline_locked()
    def _clean_output(self):
        OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
        for path in OUTPUT_DIR.iterdir():
            if path.name=='news.m3u8' or path.name.startswith(NEWS_SEGMENT_PREFIX):
                try: path.unlink()
                except OSError: pass
    def _ensure_pipeline_running(self):
        if not self._pipeline_active:
            self.start_pipeline(); return
        renderer_dead=self._renderer_popen.poll() is not None if self._renderer_popen is not None else not (self._renderer_pid and _pid_alive(self._renderer_pid))
        ffmpeg_dead=self._ffmpeg_popen.poll() is not None if self._ffmpeg_popen is not None else not (self._ffmpeg_pid and _pid_alive(self._ffmpeg_pid))
        if renderer_dead or ffmpeg_dead:
            elapsed=time.time()-self._pipeline_started_at
            news_had_output=playlist_path_has_segments(NEWS_PLAYLIST,NEWS_SEGMENT_PREFIX)
            if self._last_encoder_type=='hardware' and (elapsed < HW_ENCODER_QUICK_FAILURE_WINDOW_SECS or not news_had_output):
                self._hw_failure_count += 1
                if self._hw_failure_count >= HW_ENCODER_MAX_CONSECUTIVE_FAILURES:
                    self._hw_fallback_forced=True
                    self.logger.warning('news',f'Hardware encoder has failed {self._hw_failure_count} time(s) before producing HLS output; switching to software (libx264) fallback.')
            else:
                self._hw_failure_count=0
            self.start_pipeline()
    def is_news_buffered(self):
        if not NEWS_PLAYLIST.exists(): return False
        try: return NEWS_PLAYLIST.read_text(encoding='utf-8').count('#EXTINF:') >= 2
        except OSError: return False
    def status(self):
        return {'pipeline_active':self._pipeline_active,'renderer_running':bool(self._renderer_pid and _pid_alive(self._renderer_pid)),'ffmpeg_running':bool(self._ffmpeg_pid and _pid_alive(self._ffmpeg_pid)),'buffered':self.is_news_buffered()}
