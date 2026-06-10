from whisperquiet.control.head_gestures import NodShakeConfig, NodShakeDetector

FPS_DT = 1 / 30


def feed(detector, points, start=0.0, dt=FPS_DT):
    """points: iterable of (x, y) normalized positions. Returns the events."""
    return [detector.process(x, y, start + i * dt) for i, (x, y) in enumerate(points)]


def wave(axis, amp, center=0.5, legs=4, frames_per_leg=3):
    """Triangle wave on one axis around center; the other axis stays put."""
    values, pos, direction = [center], center, 1
    for _ in range(legs):
        target = center + direction * amp
        values += [pos + (target - pos) * i / frames_per_leg for i in range(1, frames_per_leg + 1)]
        pos, direction = target, -direction
    return [(center, v) if axis == "y" else (v, center) for v in values]


def fired(events):
    return [e for e in events if e is not None]


def test_clean_nod_fires_exactly_once_then_cooldown_blocks():
    detector = NodShakeDetector()
    events = feed(detector, wave("y", amp=0.05))
    assert fired(events) == ["nod"]
    # A second nod inside the 1.0 s cooldown must be swallowed whole.
    n = len(events)
    again = feed(detector, wave("y", amp=0.05), start=n * FPS_DT)
    assert fired(again) == []
    # And after the cooldown expires a fresh nod fires again.
    later = feed(detector, wave("y", amp=0.05), start=n * FPS_DT + 2.0)
    assert fired(later) == ["nod"]


def test_clean_shake_fires_exactly_once():
    detector = NodShakeDetector()
    events = feed(detector, wave("x", amp=0.05))
    assert fired(events) == ["shake"]


def test_slow_unidirectional_pan_never_fires():
    detector = NodShakeDetector()
    # Cursor-style panning: big total travel, zero reversals, on each axis.
    pan_right = [(0.2 + i * (0.4 / 90), 0.5) for i in range(91)]
    assert fired(feed(detector, pan_right)) == []
    detector.reset()
    pan_down = [(0.5, 0.2 + i * (0.4 / 90)) for i in range(91)]
    assert fired(feed(detector, pan_down)) == []


def test_slow_oscillation_outside_window_never_fires():
    detector = NodShakeDetector()
    # dt=0.4 makes each 3-frame leg take 1.2 s, so successive reversals land
    # 1.2 s apart — outside max_gesture_s (0.9). Lazy drifting is not a nod.
    events = feed(detector, wave("y", amp=0.05, legs=6), dt=0.4)
    assert fired(events) == []


def test_tiny_amplitude_oscillation_fires_nothing():
    detector = NodShakeDetector()
    # 0.004 swings sit below min_amplitude (0.012): camera jitter, not a nod.
    events = feed(detector, wave("y", amp=0.004, legs=12))
    assert fired(events) == []


def test_diagonal_oscillation_fires_nothing():
    detector = NodShakeDetector()
    # Equal swings on both axes: neither dominates by axis_dominance (2.0).
    points = [(y, y) for (_, y) in wave("y", amp=0.05, legs=8)]
    assert fired(feed(detector, points)) == []


def test_reset_clears_swing_state_and_cooldown():
    points = wave("y", amp=0.05)
    fire_at = feed(NodShakeDetector(), points).index("nod")

    # Stop one frame short of firing, reset, then play the rest: the
    # accumulated reversals are gone, so the tail alone must not fire.
    detector = NodShakeDetector()
    assert fired(feed(detector, points[:fire_at])) == []
    detector.reset()
    tail_start = fire_at * FPS_DT
    assert fired(feed(detector, points[fire_at:], start=tail_start)) == []

    # reset() also clears the cooldown: a nod right after one fires again.
    detector.reset()
    assert fired(feed(detector, points)) == ["nod"]
    detector.reset()
    assert fired(feed(detector, points, start=len(points) * FPS_DT)) == ["nod"]
