import math
import random

from whisperquiet.control.one_euro import OneEuroFilter


def test_first_sample_passes_through():
    f = OneEuroFilter()
    assert f(5.0, t=0.0) == 5.0


def test_smooths_jitter_at_rest():
    rng = random.Random(42)
    f = OneEuroFilter(min_cutoff=1.0, beta=0.007)
    target, dt = 100.0, 1 / 30
    outputs = [f(target + rng.uniform(-2, 2), t=i * dt) for i in range(120)]
    raw_dev = 2.0
    filtered_dev = max(abs(o - target) for o in outputs[60:])
    assert filtered_dev < raw_dev * 0.5


def test_tracks_fast_motion_with_low_lag():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.1)
    dt = 1 / 30
    out = 0.0
    for i in range(60):
        x = i * 10.0  # fast ramp: 300 units/sec
        out = f(x, t=i * dt)
    assert abs(out - 590.0) < 30.0


def test_reset_forgets_history():
    f = OneEuroFilter()
    f(0.0, t=0.0)
    f(1.0, t=0.1)
    f.reset()
    assert f(50.0, t=0.2) == 50.0


def test_non_monotonic_time_does_not_blow_up():
    f = OneEuroFilter()
    f(1.0, t=1.0)
    assert math.isfinite(f(2.0, t=1.0))
