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
    tune: Optional[str]
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
DEFAULT_HARDWARE_ACCELERATION_MODE = "software_fallback"
HARDWARE_ACCELERATION_MODES = frozenset({"software_fallback", "hardware_if_available"})

PREFERRED_PROVIDER_VIDEO_ENCODERS: dict[str, tuple[str, ...]] = {
    "nvidia": ("h264_nvenc", "hevc_nvenc", "av1_nvenc"),
    "intel": ("h264_qsv", "hevc_qsv", "av1_qsv"),
    "amd": ("h264_amf", "hevc_amf", "av1_amf"),
    "vaapi": ("h264_vaapi", "hevc_vaapi", "av1_vaapi"),
}

FFMPEG_PROFILES: dict[str, FFmpegProfile] = {
    DEFAULT_FFMPEG_PROFILE_NAME: FFmpegProfile(
        name=DEFAULT_FFMPEG_PROFILE_NAME,
        resolution="1280x720",
        video_codec="libx264",
        audio_codec="aac",
        bitrate=None,
        preset="veryfast",
        tune="zerolatency",
        hls_segment_length=6,
        encoder_type="software",
        hardware_acceleration_provider=SOFTWARE_PROVIDER.name,
    ),
}


def get_ffmpeg_profile(name: str | None) -> FFmpegProfile:
    if name and name in FFMPEG_PROFILES:
        return FFMPEG_PROFILES[name]
    return FFMPEG_PROFILES[DEFAULT_FFMPEG_PROFILE_NAME]


def resolve_ffmpeg_profile(config: dict, gpu_capabilities: dict | None = None) -> FFmpegProfile:
    profile = get_ffmpeg_profile(config.get("ffmpeg_profile"))
    acceleration_mode = normalize_hardware_acceleration_mode(config.get("hardware_acceleration_mode"))
    if acceleration_mode == "hardware_if_available":
        hardware_profile = _build_detected_hardware_profile(gpu_capabilities)
        if hardware_profile is not None:
            profile = hardware_profile
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


def normalize_hardware_acceleration_mode(value: str | None) -> str:
    mode = str(value or DEFAULT_HARDWARE_ACCELERATION_MODE).strip().lower()
    if mode not in HARDWARE_ACCELERATION_MODES:
        return DEFAULT_HARDWARE_ACCELERATION_MODE
    return mode


def _build_detected_hardware_profile(gpu_capabilities: dict | None) -> FFmpegProfile | None:
    if not isinstance(gpu_capabilities, dict):
        return None
    providers = gpu_capabilities.get("providers")
    if not isinstance(providers, dict):
        return None
    detected = gpu_capabilities.get("detected_hardware_providers")
    if not isinstance(detected, list):
        return None

    for provider_name in detected:
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            continue
        if not provider.get("available"):
            continue
        encoders = provider.get("ffmpeg_encoders")
        if not isinstance(encoders, list):
            continue
        selected_encoder = _pick_preferred_encoder(provider_name, [str(v) for v in encoders])
        if not selected_encoder:
            continue
        return FFmpegProfile(
            name=f"{provider_name}_hardware_auto",
            resolution=FFMPEG_PROFILES[DEFAULT_FFMPEG_PROFILE_NAME].resolution,
            video_codec=selected_encoder,
            audio_codec="aac",
            bitrate=None,
            preset=None,
            tune=None,
            hls_segment_length=FFMPEG_PROFILES[DEFAULT_FFMPEG_PROFILE_NAME].hls_segment_length,
            encoder_type="hardware",
            hardware_acceleration_provider=provider_name,
        )
    return None


def _pick_preferred_encoder(provider_name: str, available_encoders: list[str]) -> str | None:
    preferred = PREFERRED_PROVIDER_VIDEO_ENCODERS.get(provider_name, ())
    for encoder in preferred:
        if encoder in available_encoders:
            return encoder
    return None


def _coerce_positive_int(value, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
