"""Turns raw treadmill sensor counts into a stick value.

The model works in physical units so that every setting means one thing:

    velocity   = counts / dt                      (counts per second, from the *measured* dt)
    target     = velocity / full_speed            (hard-gated by the deadzone, clamped to [-1, 1])
    stick     += (1 - exp(-dt / tau)) * (target - stick)

The legacy filter (``stick = stick * decay + counts_per_tick * sensitivity``) tied all of its settings to the
update rate: doubling the rate halved the speed, and lowering "stop smoothness" also lowered the speed.
``from_legacy`` converts a legacy configuration into the exact equivalent of this model at the legacy rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_FULL_SPEED = 100.0
MAX_FULL_SPEED = 2_000_000.0
MAX_SMOOTHING_MS = 1000.0
MAX_DEADZONE = 0.5
MIN_UPDATE_HZ = 20
MAX_UPDATE_HZ = 500

# Below this the filtered value is snapped to exactly zero once the treadmill has stopped, so the OpenXR
# layer sees a clean "idle" instead of an endlessly decaying tail.
_SETTLE_EPSILON = 1e-3


@dataclass(frozen=True, slots=True)
class TreadmillConfig:
    full_speed: float = 40_000.0  # sensor counts per second that give full stick
    smoothing_ms: float = 80.0    # low-pass time constant
    deadzone: float = 0.01        # fraction of full_speed ignored as sensor noise
    update_hz: int = 100
    invert: bool = False          # flip the sensor direction
    allow_backward: bool = True

    def validate(self) -> None:
        if not math.isfinite(self.full_speed) or not MIN_FULL_SPEED <= self.full_speed <= MAX_FULL_SPEED:
            raise ValueError(f"Full speed must be between {MIN_FULL_SPEED:g} and {MAX_FULL_SPEED:g} counts/s.")
        if not math.isfinite(self.smoothing_ms) or not 0.0 <= self.smoothing_ms <= MAX_SMOOTHING_MS:
            raise ValueError(f"Smoothing must be between 0 and {MAX_SMOOTHING_MS:g} ms.")
        if not math.isfinite(self.deadzone) or not 0.0 <= self.deadzone <= MAX_DEADZONE:
            raise ValueError(f"Noise filter must be between 0 and {MAX_DEADZONE:g}.")
        if not MIN_UPDATE_HZ <= self.update_hz <= MAX_UPDATE_HZ:
            raise ValueError(f"Update rate must be between {MIN_UPDATE_HZ} and {MAX_UPDATE_HZ} Hz.")


def from_legacy(sensitivity: float, decay: float, deadzone: float, update_hz: int) -> TreadmillConfig:
    """Exact equivalent of a legacy (v0.1) configuration.

    Legacy steady state at velocity v:  stick = (v / hz) * sensitivity / (1 - decay)
    so full stick is reached at        v = hz * (1 - decay) / sensitivity.
    Legacy per-tick decay d corresponds to the time constant  tau = -1 / (hz * ln d).
    Legacy deadzone was counts per tick: deadzone * hz counts per second.
    """
    hz = int(min(MAX_UPDATE_HZ, max(MIN_UPDATE_HZ, update_hz)))
    decay = min(0.999, max(0.0, float(decay)))
    sensitivity = max(1e-9, float(sensitivity))

    full_speed = hz * (1.0 - decay) / sensitivity
    full_speed = min(MAX_FULL_SPEED, max(MIN_FULL_SPEED, full_speed))
    smoothing_ms = 0.0 if decay <= 0.0 else -1000.0 / (hz * math.log(decay))
    deadzone_fraction = (max(0.0, float(deadzone)) * hz) / full_speed
    return TreadmillConfig(
        full_speed=full_speed,
        smoothing_ms=min(MAX_SMOOTHING_MS, smoothing_ms),
        deadzone=min(MAX_DEADZONE, deadzone_fraction),
        update_hz=hz,
    )


class MotionModel:
    def __init__(self, config: TreadmillConfig | None = None) -> None:
        self._config = config or TreadmillConfig()
        self._stick = 0.0
        self._velocity = 0.0

    @property
    def stick(self) -> float:
        return self._stick

    @property
    def velocity(self) -> float:
        """Most recent raw belt speed in counts per second (signed, after ``invert``)."""
        return self._velocity

    def configure(self, config: TreadmillConfig) -> None:
        self._config = config

    def reset(self) -> None:
        self._stick = 0.0
        self._velocity = 0.0

    def step(self, forward_counts: float, dt: float) -> float:
        """Advance by ``dt`` seconds during which the sensor reported ``forward_counts`` (+ = forward)."""
        config = self._config
        if not math.isfinite(dt) or dt <= 0.0 or not math.isfinite(forward_counts):
            return self._stick

        counts = -forward_counts if config.invert else forward_counts
        velocity = counts / dt
        self._velocity = velocity

        if abs(velocity) < config.deadzone * config.full_speed:
            target = 0.0
        else:
            target = max(-1.0, min(1.0, velocity / config.full_speed))
        if target < 0.0 and not config.allow_backward:
            target = 0.0

        tau = config.smoothing_ms / 1000.0
        alpha = 1.0 if tau <= 0.0 else 1.0 - math.exp(-dt / tau)
        stick = self._stick + alpha * (target - self._stick)
        if target == 0.0 and abs(stick) < _SETTLE_EPSILON:
            stick = 0.0
        self._stick = max(-1.0, min(1.0, stick))
        return self._stick


def suggest_full_speed(comfortable_velocity: float, comfortable_stick: float = 0.8) -> float:
    """Calibration: the pace the user just walked at should land on ``comfortable_stick``."""
    if not math.isfinite(comfortable_velocity) or comfortable_velocity <= 0.0:
        raise ValueError("No treadmill movement was detected.")
    return min(MAX_FULL_SPEED, max(MIN_FULL_SPEED, comfortable_velocity / comfortable_stick))
