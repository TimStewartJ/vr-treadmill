from __future__ import annotations

import math
import random

import pytest

from vrtread.motion import MotionModel, TreadmillConfig, from_legacy, suggest_full_speed


def legacy_filter(counts_per_tick: list[float], sensitivity: float, decay: float, deadzone: float) -> list[float]:
    """The v0.1 engine loop, verbatim (with forward counts already sign-corrected)."""
    stick = 0.0
    out = []
    for counts in counts_per_tick:
        if abs(counts) < deadzone:
            counts = 0.0
        stick = max(-1.0, min(1.0, stick * decay + counts * sensitivity))
        out.append(stick)
    return out


LEGACY_CONFIGS = [
    pytest.param(0.0030, 0.85, 2, 100, id="balanced-preset"),
    pytest.param(0.0025, 0.82, 4, 100, id="comfort-preset"),
    pytest.param(0.0045, 0.85, 2, 120, id="faster-preset"),
    pytest.param(0.0040, 0.70, 2, 120, id="snappy-preset"),
    pytest.param(0.0010, 0.50, 4, 100, id="tims-saved-settings"),
]


@pytest.mark.parametrize("sensitivity, decay, deadzone, hz", LEGACY_CONFIGS)
def test_migrated_settings_reproduce_the_legacy_filter_exactly(sensitivity, decay, deadzone, hz):
    config = from_legacy(sensitivity, decay, deadzone, hz)
    config.validate()
    model = MotionModel(config)
    dt = 1.0 / hz

    rng = random.Random(1234)
    # Walk up to speed, hold, jitter, stop. Kept below saturation: there the new model deliberately
    # approaches 1.0 smoothly instead of snapping to it.
    peak = 0.6 * config.full_speed * dt
    counts = (
        [peak * i / 40 for i in range(40)]
        + [peak] * 60
        + [peak * rng.uniform(0.7, 1.0) for _ in range(60)]
        + [0.0] * 5
        + [float(rng.choice([0, 1, deadzone - 1, deadzone, deadzone + 1])) for _ in range(40)]
        + [0.0] * 80
    )
    expected = legacy_filter(counts, sensitivity, decay, deadzone)
    actual = [model.step(value, dt) for value in counts]

    # While walking the two filters are the same filter: bit-for-bit up to float rounding.
    walking = 40 + 60 + 60
    for index in range(walking):
        assert actual[index] == pytest.approx(expected[index], abs=1e-9), index

    # After stopping, the only difference is deliberate: the new model snaps a decaying tail below 0.001 to
    # an exact zero (the OpenXR layer's "treadmill idle"), where the legacy filter carried it on forever.
    for index in range(walking, len(counts)):
        assert actual[index] == pytest.approx(expected[index], abs=1e-3), index
    assert actual[-1] == 0.0


def test_tims_saved_settings_migrate_to_the_expected_physical_values():
    config = from_legacy(sensitivity=0.001, decay=0.5, deadzone=4, update_hz=100)
    assert config.full_speed == pytest.approx(50_000.0)
    assert config.smoothing_ms == pytest.approx(14.427, abs=1e-3)
    assert config.deadzone == pytest.approx(400.0 / 50_000.0)
    assert config.update_hz == 100


def steady_state(config: TreadmillConfig, velocity: float, seconds: float = 3.0) -> float:
    model = MotionModel(config)
    dt = 1.0 / config.update_hz
    for _ in range(int(seconds * config.update_hz)):
        model.step(velocity * dt, dt)
    return model.stick


@pytest.mark.parametrize("hz", [30, 50, 100, 144, 200, 500])
def test_walking_speed_does_not_depend_on_the_update_rate(hz):
    # The v0.1 defect: doubling "Update rate" halved the player's speed.
    config = TreadmillConfig(full_speed=40_000.0, smoothing_ms=80.0, deadzone=0.01, update_hz=hz)
    assert steady_state(config, 20_000.0) == pytest.approx(0.5, abs=1e-6)


@pytest.mark.parametrize("smoothing_ms", [0.0, 10.0, 80.0, 300.0])
def test_walking_speed_does_not_depend_on_smoothing(smoothing_ms):
    # The v0.1 defect: "Stop smoothness" 0.85 -> 0.50 cut the player's speed 3.3x.
    config = TreadmillConfig(full_speed=40_000.0, smoothing_ms=smoothing_ms)
    assert steady_state(config, 30_000.0, seconds=6.0) == pytest.approx(0.75, abs=1e-6)


def test_timer_jitter_does_not_change_walking_speed():
    config = TreadmillConfig(full_speed=40_000.0, smoothing_ms=80.0)
    model = MotionModel(config)
    rng = random.Random(7)
    values = []
    for _ in range(2000):
        dt = rng.uniform(0.004, 0.022)  # a badly behaved scheduler
        values.append(model.step(24_000.0 * dt, dt))
    tail = values[-500:]
    assert min(tail) == pytest.approx(0.6, abs=1e-6)
    assert max(tail) == pytest.approx(0.6, abs=1e-6)


def test_smoothing_is_a_real_time_constant():
    config = TreadmillConfig(full_speed=10_000.0, smoothing_ms=100.0, deadzone=0.0, update_hz=500)
    model = MotionModel(config)
    dt = 1.0 / 500
    for _ in range(50):  # exactly one time constant (100 ms)
        model.step(10_000.0 * dt, dt)
    assert model.stick == pytest.approx(1.0 - math.exp(-1.0), abs=1e-6)


def test_deadzone_gates_noise_but_not_walking():
    config = TreadmillConfig(full_speed=40_000.0, smoothing_ms=0.0, deadzone=0.02)
    model = MotionModel(config)
    assert model.step(799.0 * 0.01, 0.01) == 0.0
    assert model.step(-799.0 * 0.01, 0.01) == 0.0
    assert model.step(801.0 * 0.01, 0.01) == pytest.approx(801.0 / 40_000.0)


def test_stops_at_exactly_zero():
    config = TreadmillConfig(full_speed=40_000.0, smoothing_ms=150.0)
    model = MotionModel(config)
    for _ in range(300):
        model.step(400.0, 0.01)
    assert model.stick == pytest.approx(1.0, abs=1e-6)
    for _ in range(300):
        model.step(0.0, 0.01)
    assert model.stick == 0.0  # exact: the OpenXR layer treats this as "treadmill idle"


def test_invert_and_forward_only():
    inverted = MotionModel(TreadmillConfig(full_speed=1000.0, smoothing_ms=0.0, invert=True))
    assert inverted.step(5.0, 0.01) == pytest.approx(-0.5)
    assert inverted.velocity == pytest.approx(-500.0)

    forward_only = MotionModel(TreadmillConfig(full_speed=1000.0, smoothing_ms=0.0, allow_backward=False))
    assert forward_only.step(-5.0, 0.01) == 0.0
    assert forward_only.step(5.0, 0.01) == pytest.approx(0.5)


def test_output_is_clamped_and_survives_garbage():
    model = MotionModel(TreadmillConfig(full_speed=1000.0, smoothing_ms=0.0))
    assert model.step(1e9, 0.01) == 1.0
    assert model.step(-1e9, 0.01) == -1.0
    before = model.stick
    assert model.step(float("nan"), 0.01) == before
    assert model.step(10.0, 0.0) == before
    assert model.step(10.0, -1.0) == before
    assert model.step(10.0, float("inf")) == before


def test_live_reconfiguration_keeps_state():
    model = MotionModel(TreadmillConfig(full_speed=1000.0, smoothing_ms=0.0))
    assert model.step(5.0, 0.01) == pytest.approx(0.5)
    model.configure(TreadmillConfig(full_speed=2000.0, smoothing_ms=0.0))
    assert model.step(5.0, 0.01) == pytest.approx(0.25)
    model.reset()
    assert model.stick == 0.0


def test_calibration_suggestion():
    assert suggest_full_speed(32_000.0) == pytest.approx(40_000.0)
    assert suggest_full_speed(32_000.0, comfortable_stick=1.0) == pytest.approx(32_000.0)
    with pytest.raises(ValueError):
        suggest_full_speed(0.0)
    with pytest.raises(ValueError):
        suggest_full_speed(float("nan"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"full_speed": 0.0},
        {"full_speed": float("nan")},
        {"smoothing_ms": -1.0},
        {"deadzone": 0.9},
        {"update_hz": 0},
        {"update_hz": 100_000},
    ],
)
def test_validation_rejects_nonsense(kwargs):
    with pytest.raises(ValueError):
        TreadmillConfig(**kwargs).validate()
