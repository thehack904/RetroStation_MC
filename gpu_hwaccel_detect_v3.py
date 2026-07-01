#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys


def _ensure_app_importable() -> None:
    """Add the script's parent directory to sys.path when needed.

    This allows the script to be run from any working directory, e.g.::

        sudo python3 /home/iptv/retrostation-mc/gpu_hwaccel_detect_v3.py --test
    """
    from pathlib import Path

    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)


_ensure_app_importable()

from app.gpu_capabilities import detect_gpu_capabilities  # noqa: E402


def _build_text_summary(capabilities: dict) -> str:
    device_providers = capabilities.get("device_detected_providers") or []
    ready_providers = capabilities.get("detected_hardware_providers") or []
    encoders = capabilities.get("ffmpeg_detected_encoders") or []
    usable = bool(ready_providers)
    lines = [
        "RetroStation MC GPU Hardware Acceleration Detection (v3)",
        f"Hardware acceleration usable: {'yes' if usable else 'no'}",
        f"Detected GPU providers: {', '.join(device_providers) if device_providers else 'none'}",
        f"Hardware-ready providers: {', '.join(ready_providers) if ready_providers else 'none'}",
        f"Detected GPU encoders: {', '.join(encoders) if encoders else 'none'}",
        f"Status: {capabilities.get('message') or 'Unknown'}",
    ]
    return "\n".join(lines)


def _build_test_summary(capabilities: dict) -> str:
    """Return a verbose per-provider/encoder diagnostic for the --test mode."""
    lines = ["RetroStation MC GPU Hardware Acceleration Detection (v3) — Test Mode"]
    lines.append("")

    providers: dict = capabilities.get("providers") or {}
    if providers:
        lines.append("Provider details:")
        for name, info in providers.items():
            label = info.get("label") or name
            device_vis = info.get("device_visible")
            available = info.get("available")
            encoders = info.get("ffmpeg_encoders") or []
            probe_reason = info.get("functional_probe_reason") or ""
            vis_reason = info.get("visibility_reason") or ""
            status_icon = "PASS" if available else "FAIL"
            lines.append(f"  [{status_icon}] {label}")
            lines.append(f"        Device visible : {'yes' if device_vis else 'no'} — {vis_reason}")
            lines.append(f"        FFmpeg encoders: {', '.join(encoders) if encoders else 'none'}")
            if probe_reason:
                lines.append(f"        Probe result   : {probe_reason}")
    else:
        lines.append("No provider details available.")

    lines.append("")
    lines.append(_build_text_summary(capabilities))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect whether RetroStation MC can use hardware acceleration."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output full detection details as JSON.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run a verbose per-provider diagnostic and exit 1 if no hardware encoder is usable.",
    )
    args = parser.parse_args(argv)
    try:
        capabilities = detect_gpu_capabilities()
    except Exception as exc:
        print(f"GPU hardware acceleration detection failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(capabilities, indent=2, sort_keys=True))
        return 0
    if args.test:
        print(_build_test_summary(capabilities))
        hardware_usable = bool(capabilities.get("detected_hardware_providers"))
        return 0 if hardware_usable else 1
    print(_build_text_summary(capabilities))
    return 0


if __name__ == "__main__":
    sys.exit(main())
