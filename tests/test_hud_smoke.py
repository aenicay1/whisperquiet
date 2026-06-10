"""Headless smoke tests for the camera-control HUD.

Only the pure module-level logic is exercised — no NSPanel/NSView is ever
instantiated (pytest runs without a GUI session)."""

import pytest

from whisperquiet.vision.hud import (
    FLASH_LABELS,
    GESTURE_METERS,
    HUD,
    STATUS_COLORS,
    UpdateThrottle,
    clamp01,
    flash_label,
    format_metrics,
    meter_fill_color,
    status_color,
    status_label,
)


def test_status_color_table_covers_all_modes():
    for mode in ("idle", "listening", "scroll", "calibrating", "cursor", "camera off"):
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


def test_cursor_status_is_purple_pill():
    assert "cursor" in STATUS_COLORS
    r, g, b, a = status_color("cursor")
    assert b > g and r > g  # purple: red+blue dominate green
    assert a == 1.0
    assert status_label("cursor") == "[CURSOR]"


def test_clamp01():
    assert clamp01(-0.5) == 0.0
    assert clamp01(0.0) == 0.0
    assert clamp01(0.37) == 0.37
    assert clamp01(1.0) == 1.0
    assert clamp01(1.5) == 1.0


def test_meter_fill_color_threshold_behavior():
    below = meter_fill_color(0.49, 0.5)
    at = meter_fill_color(0.5, 0.5)
    above = meter_fill_color(0.9, 0.5)
    # At/above threshold: the accent green (same as the listening pill)
    assert at == above == STATUS_COLORS["listening"]
    r, g, b, _ = at
    assert g > r and g > b
    # Below threshold: teal — green and blue dominate red, and not the green
    assert below != at
    r, g, b, _ = below
    assert g > r and b > r
    # Zero threshold means always-active
    assert meter_fill_color(0.0, 0.0) == STATUS_COLORS["listening"]


def test_meter_fill_color_is_rgba_tuple():
    for rgba in (meter_fill_color(0.1, 0.5), meter_fill_color(0.9, 0.5)):
        assert isinstance(rgba, tuple) and len(rgba) == 4
        assert all(0.0 <= c <= 1.0 for c in rgba)


def test_flash_label_known_events():
    assert flash_label("left_click") == "LEFT CLICK"
    assert flash_label("right_click") == "RIGHT CLICK"
    assert flash_label("drag_start") == "DRAG"
    assert flash_label("drag_end") == "DROP"
    assert flash_label("scroll_up") == "SCROLL ↑"
    assert flash_label("scroll_down") == "SCROLL ↓"


def test_flash_label_unknown_event_is_uppercased():
    assert flash_label("hold") == "HOLD"
    assert flash_label("Triple Blink") == "TRIPLE BLINK"


def test_flash_label_idempotent_on_already_mapped_text():
    # flash_event() maps its input, so feeding it pre-mapped text must be safe
    for event in FLASH_LABELS:
        assert flash_label(flash_label(event)) == flash_label(event)


def test_gesture_meters_cover_five_gestures():
    assert [label for _, label in GESTURE_METERS] == [
        "L·WINK",
        "R·WINK",
        "BROW",
        "PUCKER",
        "JAW",
    ]
    assert len({key for key, _ in GESTURE_METERS}) == 5


def test_gesture_throttle_is_separate_15hz_instance():
    # HUD.__init__ is pure Python — no NSPanel is created until shown
    hud = HUD()
    assert hud._meter_throttle is not hud._throttle
    # ~15Hz: a 30Hz burst gets every other sample dropped
    t = hud._meter_throttle
    dt = 1.0 / 30.0
    accepted = [t.should_accept(now=i * dt) for i in range(4)]
    assert accepted == [True, False, True, False]
    # while the landmark throttle still passes a steady 30Hz stream
    lt = hud._throttle
    assert all(lt.should_accept(now=i * dt) for i in range(4))


def test_gesture_throttle_passes_steady_15hz():
    t = UpdateThrottle(15.0)
    dt = 1.0 / 15.0
    assert sum(t.should_accept(now=i * dt) for i in range(15)) == 15


@pytest.mark.skip(reason="needs GUI session")
def test_panel_renders_landmarks():
    """Would alloc/init the HUD NSPanel and pump update_landmarks — requires
    a real window server session, so it never runs under plain pytest."""


@pytest.mark.skip(reason="needs GUI session")
def test_panel_renders_meters_and_flash():
    """Would alloc/init the HUD NSPanel, pump set_gesture_levels and
    flash_event — requires a real window server session."""
