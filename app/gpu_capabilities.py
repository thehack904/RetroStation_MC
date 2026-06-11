from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


PROVIDER_ENCODERS: dict[str, tuple[str, ...]] = {
    "nvidia": ("h264_nvenc", "hevc_nvenc", "av1_nvenc"),
    "intel": ("h264_qsv", "hevc_qsv", "av1_qsv"),
    "amd": ("h264_amf", "hevc_amf", "av1_amf"),
    "vaapi": ("h264_vaapi", "hevc_vaapi", "av1_vaapi"),
}

PROVIDER_LABELS: dict[str, str] = {
    "nvidia": "NVIDIA NVENC",
    "intel": "Intel QuickSync / QSV",
    "amd": "AMD AMF",
    "vaapi": "VAAPI",
}

PROVIDER_DETECTION_COMMANDS: dict[str, tuple[str, ...]] = {
    "nvidia": ("nvidia-smi",),
    "intel": ("vainfo",),
    "amd": ("rocm-smi",),
    "vaapi": ("vainfo",),
}

INTEL_DRM_DRIVERS = frozenset({"i915", "xe"})
AMD_DRM_DRIVERS = frozenset({"amdgpu", "radeon"})
VAAPI_DRM_DRIVERS = frozenset(set(INTEL_DRM_DRIVERS) | set(AMD_DRM_DRIVERS))


def detect_gpu_capabilities(*, ffmpeg_bin: str = "ffmpeg") -> dict:
    ffmpeg_info = _probe_ffmpeg_encoders(ffmpeg_bin)
    in_docker = _running_in_docker()
    providers: dict[str, dict] = {}
    detected_hardware_providers: list[str] = []
    docker_visible_providers: list[str] = []
    ffmpeg_detected_encoders: list[str] = []

    for name, encoders in PROVIDER_ENCODERS.items():
        provider_ffmpeg_encoders = sorted([encoder for encoder in encoders if encoder in ffmpeg_info["encoders"]])
        device_visible, visibility_reason = _provider_device_visible(name, in_docker=in_docker)
        if device_visible:
            ffmpeg_detected_encoders.extend(provider_ffmpeg_encoders)
        if device_visible:
            docker_visible_providers.append(name)
        command_results = {cmd: _command_available(cmd) for cmd in PROVIDER_DETECTION_COMMANDS.get(name, ())}
        available = device_visible and bool(provider_ffmpeg_encoders)
        if available:
            detected_hardware_providers.append(name)
        provider_label = _provider_label(name)
        providers[name] = {
            "name": name,
            "label": provider_label,
            "available": available,
            "device_visible": device_visible,
            "visibility_reason": visibility_reason,
            "ffmpeg_encoders": provider_ffmpeg_encoders,
            "detection_commands": command_results,
        }

    return {
        "running_in_docker": in_docker,
        "hardware_available": bool(detected_hardware_providers),
        "detected_hardware_providers": detected_hardware_providers,
        "docker_visible_providers": docker_visible_providers if in_docker else [],
        "ffmpeg_available": ffmpeg_info["available"],
        "ffmpeg_error": ffmpeg_info["error"],
        "ffmpeg_detected_encoders": sorted(set(ffmpeg_detected_encoders)),
        "providers": providers,
        "software_fallback": {
            "available": True,
            "label": "software",
            "reason": "Software fallback is always available (libx264).",
        },
        "message": (
            "Hardware acceleration detected."
            if detected_hardware_providers
            else "No hardware acceleration detected; software fallback is active."
        ),
    }


@lru_cache(maxsize=2)
def _probe_ffmpeg_encoders(ffmpeg_bin: str) -> dict:
    if not ffmpeg_bin:
        return {"available": False, "error": "ffmpeg binary path is empty", "encoders": frozenset()}
    if shutil.which(ffmpeg_bin) is None:
        return {"available": False, "error": f"{ffmpeg_bin!r} not found in PATH", "encoders": frozenset()}
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return {"available": False, "error": str(exc), "encoders": frozenset()}

    output = f"{proc.stdout}\n{proc.stderr}"
    encoders = {
        parts[1]
        for line in output.splitlines()
        if line.startswith(" ")
        for parts in [line.split()]
        if len(parts) >= 2
    }
    if proc.returncode != 0:
        return {
            "available": bool(encoders),
            "error": f"ffmpeg encoder query exited with status {proc.returncode}",
            "encoders": frozenset(encoders),
        }
    return {"available": True, "error": "", "encoders": frozenset(encoders)}


def _provider_device_visible(provider: str, *, in_docker: bool) -> tuple[bool, str]:
    if provider == "nvidia":
        visible = os.getenv("NVIDIA_VISIBLE_DEVICES", "").strip().lower()
        if in_docker and visible in {"none", "void"}:
            return False, "NVIDIA_VISIBLE_DEVICES disables GPUs inside Docker."
        if any(Path(path).exists() for path in ("/dev/nvidiactl", "/dev/nvidia0")):
            return True, "NVIDIA device nodes are visible."
        return False, "No NVIDIA device nodes detected."

    if provider == "amd":
        if Path("/dev/kfd").exists():
            return True, "AMD ROCm KFD device is visible."
        if _dri_device_visible() and _driver_matches(AMD_DRM_DRIVERS):
            return True, "DRI render device with AMD DRM driver is visible."
        return False, "No AMD-compatible device nodes detected."

    if provider in {"intel", "vaapi"}:
        if provider == "intel" and _dri_device_visible() and _driver_matches(INTEL_DRM_DRIVERS):
            return True, "DRI render device with Intel DRM driver is visible."
        if provider == "vaapi" and _dri_device_visible() and _driver_matches(VAAPI_DRM_DRIVERS):
            return True, "DRI render device with VAAPI-capable DRM driver is visible."
        if provider == "vaapi" and _dri_device_visible() and not _drm_kernel_drivers():
            return True, "DRI render device is visible."
        return False, "No DRI render device nodes detected."

    return False, "Unknown provider."


def _dri_device_visible() -> bool:
    dri = Path("/dev/dri")
    if not dri.exists() or not dri.is_dir():
        return False
    return any(node.name.startswith(("renderD", "card")) for node in dri.iterdir())


@lru_cache(maxsize=1)
def _drm_kernel_drivers() -> frozenset[str]:
    drivers: set[str] = set()
    for card_path in Path("/sys/class/drm").glob("card[0-9]*"):
        driver_path = card_path / "device" / "driver"
        if not driver_path.exists():
            continue
        try:
            driver_name = driver_path.resolve().name.strip().lower()
        except OSError:
            continue
        if driver_name:
            drivers.add(driver_name)
    return frozenset(drivers)


def _driver_matches(drivers: frozenset[str]) -> bool:
    detected = _drm_kernel_drivers()
    return bool(detected.intersection(drivers))


def _provider_label(provider: str) -> str:
    if provider != "vaapi":
        return PROVIDER_LABELS[provider]
    drivers = _drm_kernel_drivers()
    intel = bool(drivers.intersection(INTEL_DRM_DRIVERS))
    amd = bool(drivers.intersection(AMD_DRM_DRIVERS))
    if intel and amd:
        return "Intel/AMD VAAPI"
    if intel:
        return "Intel VAAPI"
    if amd:
        return "AMD VAAPI"
    return PROVIDER_LABELS[provider]


def _running_in_docker() -> bool:
    if Path("/.dockerenv").exists():
        return True
    cgroup_path = Path("/proc/1/cgroup")
    try:
        content = cgroup_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    markers = ("docker", "kubepods", "containerd")
    return any(marker in content for marker in markers)


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None
