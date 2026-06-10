"""Headless smoke tests for the camera-control HUD.

Only the pure module-level logic is exercised — no NSPanel/NSView is ever
instantiated (pytest runs without a GUI session)."""

import pytest

from whisperquiet.vision.hud import (
    STATUS_COLORS,
    UpdateThrottle,
    format_metrics,
    status_color,
    status_label,
)


def test_status_color_table_covers_all_modes():
    for mode in ("idle", "listening", "scroll", "calibrating", "camera off"):
        rgba = status_color(mode)
        assert rgba == STATUS_COLORS[mode]
        assert len(rgba) == 4
        assert all(0.0 <= c <= 1.0 for c in rgba)


def test_status_accents_match_spec():
    r, g, b, _ = status_color("listening")
    assert g > r and g > b  # green
    r, g, b, _ = status_color("scroll")
    assert b > r and b > g  # blue
    r, g, b, _ = status_color("calibrating")
    assert r > b and g > b  # orange
    r, g, b, _ = status_color("idle")
    assert abs(r - g) < 0.1 and abs(g - b) < 0.1  # gray
    assert status_color("camera off") == status_color("idle")


def test_unknown_mode_falls_back_to_idle_gray():
    assert status_color("warp drive") == STATUS_COLORS["idle"]


def test_status_label_is_bracketed_uppercase():
    assert status_label("listening") == "[LISTENING]"
    assert status_label("camera off") == "[CAMERA OFF]"


def test_format_metrics():
    assert format_metrics(31.0, 12.0) == "31 fps · 12 ms"
    assert format_metrics(29.6, 11.5) == "30 fps · 12 ms"


def test_throttle_accepts_first_sample():
    t = UpdateThrottle(30.0)
    assert t.should_accept(now=0.0)


def test_throttle_drops_faster_than_max_hz():
    t = UpdateThrottle(30.0)
    assert t.should_accept(now=0.0)
    assert not t.should_accept(now=0.010)  # 100Hz burst → dropped
    assert not t.should_accept(now=0.020)
    assert t.should_accept(now=0.040)  # past 1/30s since last accept


def test_throttle_passes_steady_30hz():
    t = UpdateThrottle(30.0)
    dt = 1.0 / 30.0
    accepted = sum(t.should_accept(now=i * dt) for i in range(30))
    assert accepted == 30


def test_throttle_dropped_samples_do_not_reset_window():
    t = UpdateThrottle(30.0)
    assert t.should_accept(now=0.0)
    for i in range(1, 6):
        assert not t.should_accept(now=i * 0.005)  # hammering at 200Hz
    assert t.should_accept(now=1.0 / 30.0 + 0.001)


@pytest.mark.skip(reason="needs GUI session")
def test_panel_renders_landmarks():
    """Would alloc/init the HUD NSPanel and pump update_landmarks — requires
    a real window server session, so it never runs under plain pytest."""
