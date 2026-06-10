import random

from whisperquiet.control.head_cursor import CursorConfig, HeadCursor

FPS_DT = 1 / 30


def feed(cursor, points, start=0.0, dt=FPS_DT):
    """points: iterable of (x, y) normalized positions. Returns the deltas."""
    return [cursor.process(x, y, start + i * dt) for i, (x, y) in enumerate(points)]


def sweep(x0, x1, y, frames):
    step = (x1 - x0) / frames
    return [(x0 + i * step, y) for i in range(frames + 1)]


def total(deltas):
    moved = [d for d in deltas if d is not None]
    return sum(d[0] for d in moved), sum(d[1] for d in moved)


def test_first_frame_returns_none():
    cursor = HeadCursor()
    assert cursor.process(0.5, 0.5, 0.0) is None


def test_jitter_at_rest_produces_zero_net_motion():
    rng = random.Random(42)
    cursor = HeadCursor()
    points = [
        (0.5 + rng.uniform(-0.001, 0.001), 0.5 + rng.uniform(-0.001, 0.001))
        for _ in range(120)
    ]
    dx, dy = total(feed(cursor, points))
    assert (dx, dy) == (0.0, 0.0)  # every frame lands in the deadzone


def test_steady_sweep_right_moves_cursor_right_mirrored():
    cursor = HeadCursor()
    # dx is negated for the mirrored-camera frame: moving the head right
    # makes the nose's normalized x DECREASE, so cursor-right (positive
    # screen dx) comes from shrinking x. This sweep grows x, so every
    # emitted dx must be negative.
    deltas = feed(cursor, sweep(0.3, 0.6, y=0.5, frames=90))
    moved = [d for d in deltas if d is not None]
    assert moved, "a steady sweep must emit deltas"
    assert all(d[0] < 0 for d in moved)
    dx, _ = total(deltas)
    # Roughly proportional to gain * travel (One-Euro lag eats a little).
    expected = -CursorConfig().gain_px * 0.3
    assert expected * 1.05 < dx < expected * 0.6


def test_steady_sweep_down_gives_positive_dy():
    cursor = HeadCursor()
    points = [(0.5, 0.3 + i * (0.3 / 90)) for i in range(91)]
    deltas = feed(cursor, points)
    moved = [d for d in deltas if d is not None]
    assert moved and all(d[1] > 0 for d in moved)  # nose y grows = screen down
    _, dy = total(deltas)
    expected = CursorConfig().gain_px * 0.3
    assert expected * 0.6 < dy < expected * 1.05


def test_precision_mode_scales_deltas_down():
    cfg = CursorConfig()
    normal, precise = HeadCursor(cfg), HeadCursor(cfg)
    precise.set_precision(True)
    points = sweep(0.3, 0.6, y=0.5, frames=90)
    dx_n, _ = total(feed(normal, points))
    dx_p, _ = total(feed(precise, points))
    assert abs(dx_p / dx_n - cfg.precision_scale) < 1e-9


def test_reset_forgets_previous_position_no_jump():
    cursor = HeadCursor()
    feed(cursor, [(0.2, 0.2)] * 5)
    cursor.reset()
    # A far-away point right after reset is a fresh first frame, not a jump.
    assert cursor.process(0.8, 0.8, 1.0) is None
    # And the following near-rest frame stays inside the deadzone.
    assert cursor.process(0.8, 0.8, 1.0 + FPS_DT) is None
