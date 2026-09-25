from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path


def _drm_vendor_ids() -> set[str]:
    vendors: set[str] = set()
    for card in Path('/sys/class/drm').glob('card[0-9]*'):
        vendor_path = card / 'device' / 'vendor'
        try:
            value = vendor_path.read_text(encoding='utf-8').strip().lower()
        except OSError:
            continue
        if value:
            vendors.add(value)
    return vendors


def _candidate_drivers() -> list[str]:
    explicit = str(os.environ.get('RSMC_VAAPI_DRIVER') or '').strip()
    inherited = str(os.environ.get('LIBVA_DRIVER_NAME') or '').strip()
    candidates: list[str] = []
    for value in (explicit, inherited):
        if value and value not in candidates:
            candidates.append(value)

    vendors = _drm_vendor_ids()
    if '0x8086' in vendors:  # Intel
        # i965 is required by older Intel generations such as Ivy Bridge;
        # iHD is the normal driver for newer Intel generations. Probe both.
        ordered = ('i965', 'iHD')
    elif '0x1002' in vendors:  # AMD
        ordered = ('radeonsi',)
    else:
        ordered = ('i965', 'iHD', 'radeonsi')
    for value in ordered:
        if value not in candidates:
            candidates.append(value)
    return candidates


def _probe_driver(driver: str, *, ffmpeg_bin: str = 'ffmpeg') -> bool:
    if not Path('/dev/dri/renderD128').exists():
        return False
    env = os.environ.copy()
    env['LIBVA_DRIVER_NAME'] = driver
    cmd = [
        ffmpeg_bin, '-hide_banner', '-loglevel', 'error',
        '-vaapi_device', os.environ.get('RSMC_VAAPI_DEVICE', '/dev/dri/renderD128'),
        '-f', 'lavfi', '-i', 'nullsrc=s=720x480:r=30',
        '-vf', 'format=nv12,hwupload',
        '-c:v', 'h264_vaapi', '-frames:v', '1', '-f', 'null', '-',
    ]
    try:
        proc = subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@lru_cache(maxsize=1)
def configure_vaapi_driver(*, ffmpeg_bin: str = 'ffmpeg') -> str:
    """Detect and pin a functional VA-API userspace driver for this RSMC process.

    The selected value is exported into this process environment so every child
    FFmpeg process (Guide, virtual channels, and HDHomeRun Testing) inherits the
    same working libva driver. Explicit RSMC_VAAPI_DRIVER/LIBVA_DRIVER_NAME
    values are tried first, but still validated before use.
    """
    for driver in _candidate_drivers():
        if _probe_driver(driver, ffmpeg_bin=ffmpeg_bin):
            os.environ['LIBVA_DRIVER_NAME'] = driver
            os.environ['RSMC_VAAPI_DRIVER'] = driver
            return driver
    return ''
