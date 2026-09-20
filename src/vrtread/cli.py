from __future__ import annotations

import argparse
import sys
import time

from .capture import CaptureConfig, CaptureMode, list_mouse_devices
from .engine import TreadmillEngine
from .motion import TreadmillConfig
from .openxr import ensure_layer_enabled
from .outputs import OpenXrCombineMode, OpenXrFilterMode, OutputConfig, OutputMode
from .settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the VR Treadmill engine from a terminal. Options default to the saved GUI settings."
    )
    parser.add_argument("--full-speed", type=float, help="Sensor counts per second that give full stick.")
    parser.add_argument("--smoothing-ms", type=float, help="Low-pass time constant in milliseconds.")
    parser.add_argument("--deadzone", type=float, help="Fraction of full speed ignored as noise (0-0.5).")
    parser.add_argument("--update-hz", type=int)
    parser.add_argument("--invert", action="store_true", default=None, help="Flip the sensor direction.")
    parser.add_argument("--output-mode", choices=[mode.value for mode in OutputMode])
    parser.add_argument("--openxr-filter", choices=[mode.value for mode in OpenXrFilterMode])
    parser.add_argument("--openxr-combine", choices=[mode.value for mode in OpenXrCombineMode])
    parser.add_argument("--capture", choices=[mode.value for mode in CaptureMode])
    parser.add_argument("--device", help="Raw Input device path of the treadmill sensor (see --list-devices).")
    parser.add_argument("--list-devices", action="store_true", help="List connected mice and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_devices:
        for device in list_mouse_devices():
            print(f"{device.label}\n    {device.path}")
        return 0

    settings = load_settings()
    base = settings.treadmill
    config = TreadmillConfig(
        full_speed=args.full_speed if args.full_speed is not None else base.full_speed,
        smoothing_ms=args.smoothing_ms if args.smoothing_ms is not None else base.smoothing_ms,
        deadzone=args.deadzone if args.deadzone is not None else base.deadzone,
        update_hz=args.update_hz if args.update_hz is not None else base.update_hz,
        invert=bool(args.invert) if args.invert is not None else base.invert,
        allow_backward=base.allow_backward,
    )
    output_config = OutputConfig(
        mode=OutputMode(args.output_mode) if args.output_mode else settings.output_mode,
        openxr_filter_mode=OpenXrFilterMode(args.openxr_filter) if args.openxr_filter else settings.openxr_filter_mode,
        openxr_combine_mode=OpenXrCombineMode(args.openxr_combine) if args.openxr_combine else settings.openxr_combine_mode,
        openxr_drive_inactive=settings.openxr_drive_inactive,
    )
    capture_config = CaptureConfig(
        mode=CaptureMode(args.capture) if args.capture else settings.capture.mode,
        device=args.device if args.device is not None else settings.capture.device,
    )

    engine = TreadmillEngine(config, output_config, capture_config)
    try:
        config.validate()
        if output_config.mode.uses_openxr:
            print(ensure_layer_enabled().message)
        engine.start()
        print(f"Running: output={output_config.mode.value}, capture={engine.status().capture}. Press Ctrl+C to stop.")
        while engine.is_running:
            status = engine.status()
            print(f"\rStick Y: {status.stick_y:+.3f}   belt: {status.velocity:+9.0f} counts/s ", end="", flush=True)
            time.sleep(0.1)
        error = engine.status().last_error
        if error:
            print(f"\nError: {error}", file=sys.stderr)
            return 1
    except KeyboardInterrupt:
        print()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.stop()
        print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
