from __future__ import annotations

import argparse
import sys
import time

from .engine import TreadmillConfig, TreadmillEngine
from .openxr import ensure_automatic_layer
from .outputs import OpenXrFilterMode, OutputConfig, OutputMode


def build_parser() -> argparse.ArgumentParser:
    defaults = TreadmillConfig()
    parser = argparse.ArgumentParser(description="Run mouse treadmill to VR locomotion output.")
    parser.add_argument("--sensitivity", type=float, default=defaults.sensitivity)
    parser.add_argument("--decay", type=float, default=defaults.decay)
    parser.add_argument("--deadzone", type=int, default=defaults.deadzone)
    parser.add_argument("--update-hz", type=int, default=defaults.update_hz)
    parser.add_argument(
        "--output-mode",
        choices=[mode.value for mode in OutputMode],
        default=OutputMode.OPENXR.value,
        help="Where to publish treadmill movement.",
    )
    parser.add_argument(
        "--openxr-filter",
        choices=[mode.value for mode in OpenXrFilterMode],
        default=OpenXrFilterMode.BALANCED.value,
        help="OpenXR action filtering policy.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = TreadmillConfig(
        sensitivity=args.sensitivity,
        decay=args.decay,
        deadzone=args.deadzone,
        update_hz=args.update_hz,
    )
    output_config = OutputConfig(
        mode=OutputMode(args.output_mode),
        openxr_filter_mode=OpenXrFilterMode(args.openxr_filter),
    )
    engine = TreadmillEngine(config, output_config)

    try:
        if output_config.mode.uses_openxr:
            status = ensure_automatic_layer()
            print(status.message)
        engine.start()
        print(
            f"Mouse-to-VR treadmill running in {output_config.mode.value} mode "
            f"with {output_config.openxr_filter_mode.value} OpenXR filtering."
        )
        print("Cursor is locked to screen center.")
        print("Press Ctrl+C to stop.")
        while engine.is_running:
            status = engine.status()
            if status.last_error:
                print(f"\nError: {status.last_error}", file=sys.stderr)
                return 1
            print(f"\rStick Y: {status.stick_y:+.3f}", end="", flush=True)
            time.sleep(0.1)
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
