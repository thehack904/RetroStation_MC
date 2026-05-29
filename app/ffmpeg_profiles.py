from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class FFmpegHardwareAccelerationProvider:
    name: str
    label: str
    placeholder: bool = False


@dataclass(frozen=True)
class FFmpegProfile:
    name: str
    resolution: str
    video_codec: str
    audio_codec: str
    bitrate: Optional[str]
    preset: Optional[str]
    hls_segment_length: int
    encoder_type: str
    hardware_acceleration_provider: Optional[str]


SOFTWARE_PROVIDER = FFmpegHardwareAccelerationProvider(name="software", label="Software")

HARDWARE_ACCELERATION_PROVIDERS: dict[str, FFmpegHardwareAccelerationProvider] = {
    SOFTWARE_PROVIDER.name: SOFTWARE_PROVIDER,
    "nvidia": FFmpegHardwareAccelerationProvider(name="nvidia", label="NVIDIA", placeholder=True),
    "intel": FFmpegHardwareAccelerationProvider(name="intel", label="Intel", placeholder=True),
    "amd": FFmpegHardwareAccelerationProvider(name="amd", label="AMD", placeholder=True),
    "vaapi": FFmpegHardwareAccelerationProvider(name="vaapi", label="VAAPI", placeholder=True),
}

DEFAULT_FFMPEG_PROFILE_NAME = "software_default"

FFMPEG_PROFILES: dict[str, FFmpegProfile] = {
    DEFAULT_FFMPEG_PROFILE_NAME: FFmpegProfile(
        name=DEFAULT_FFMPEG_PROFILE_NAME,
        resolution="1280x720",
        video_codec="libx264",
        audio_codec="aac",
        bitrate=None,
        preset="veryfast",
        hls_segment_length=6,
        encoder_type="software",
        hardware_acceleration_provider=SOFTWARE_PROVIDER.name,
    ),
}


def get_ffmpeg_profile(name: str | None) -> FFmpegProfile:
    if name and name in FFMPEG_PROFILES:
        return FFMPEG_PROFILES[name]
    return FFMPEG_PROFILES[DEFAULT_FFMPEG_PROFILE_NAME]


def resolve_ffmpeg_profile(config: dict) -> FFmpegProfile:
    profile = get_ffmpeg_profile(config.get("ffmpeg_profile"))
    resolution = str(config.get("resolution", profile.resolution) or profile.resolution)
    segment_length = _coerce_positive_int(
        config.get("segment_seconds", profile.hls_segment_length),
        default=profile.hls_segment_length,
    )
    return replace(
        profile,
        resolution=resolution,
        hls_segment_length=segment_length,
    )


def _coerce_positive_int(value, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
