"""Camera-control heads-up display.

A non-activating NSPanel pinned to the top-right of the main screen: status
pill, live face-wireframe scatter, per-gesture level meters, fps/latency
line, a calibration progress bar, and a transient event toast. Same
threading contract as overlay.py — all AppKit work is marshalled to the
main thread; public methods are safe to call from worker threads.

The pure pieces (status→color table, label/metrics formatting, the update
throttles, meter coloring, flash-label mapping) live at module level so
they can be unit-tested headless.
"""

from __future__ import annotations

import time

import AppKit
import numpy as np
import objc
from PyObjCTools import AppHelper

_WIDTH, _HEIGHT, _MARGIN = 240, 360, 12
_PILL_H, _BAR_H = 24, 3
_INNER_W = _WIDTH - 2 * _MARGIN

_LANDMARK_MAX_HZ = 30.0
_GESTURE_MAX_HZ = 15.0
_DOT_RADIUS = 1.75

# Gesture meter block (between the wireframe canvas and the metrics line).
# Display order is top-to-bottom; keys are what set_gesture_levels expects.
GESTURE_METERS: tuple[tuple[str, str], ...] = (
    ("l_wink", "L·WINK"),
    ("r_wink", "R·WINK"),
    ("brow", "BROW"),
    ("pucker", "PUCKER"),
    ("jaw", "JAW"),
)
_METER_ROW_H = 16
_METER_LABEL_W = 56
_METER_TRACK_H = 3
_METER_TICK_W, _METER_TICK_H = 2, 9
_METER_BASE_Y = 30
_METER_BLOCK_H = len(GESTURE_METERS) * _METER_ROW_H
_METER_TRACK_W = _INNER_W - _METER_LABEL_W

# Event toast fade.
_FLASH_DURATION_S = 0.6
_FLASH_STEP_S = 1.0 / 30.0

# Accent per status mode, RGBA in 0..1 (kept as plain tuples so the mapping
# is testable without AppKit).
STATUS_COLORS: dict[str, tuple[float, float, float, float]] = {
    "listening": (0.20, 0.84, 0.49, 1.0),  # green
    "scroll": (0.04, 0.52, 1.00, 1.0),  # blue
    "calibrating": (1.00, 0.62, 0.04, 1.0),  # orange
    "cursor": (0.68, 0.45, 1.0, 1.0),  # purple
    "idle": (0.62, 0.62, 0.66, 1.0),  # gray
    "camera off": (0.62, 0.62, 0.66, 1.0),  # gray
}
_WIREFRAME_RGBA = (0.25, 0.87, 0.82, 0.9)  # cyan/teal
_METER_ACTIVE_RGBA = STATUS_COLORS["listening"]  # accent green

# Event value → toast text. Unknown events fall back to uppercased input.
FLASH_LABELS: dict[str, str] = {
    "left_click": "LEFT CLICK",
    "right_click": "RIGHT CLICK",
    "drag_start": "DRAG",
    "drag_end": "DROP",
    "scroll_up": "SCROLL ↑",
    "scroll_down": "SCROLL ↓",
}


def status_color(mode: str) -> tuple[float, float, float, float]:
    """Accent RGBA for a status mode; unknown modes fall back to idle gray."""
    return STATUS_COLORS.get(mode, STATUS_COLORS["idle"])


def status_label(mode: str) -> str:
    return f"[{mode.upper()}]"


def format_metrics(fps: float, latency_ms: float) -> str:
    return f"{fps:.0f} fps · {latency_ms:.0f} ms"


def clamp01(x: float) -> float:
    """Clamp a scalar to [0, 1]."""
    return max(0.0, min(1.0, float(x)))


def meter_fill_color(score: float, threshold: float) -> tuple:
    """Fill RGBA for a gesture meter: accent green once the score reaches
    its threshold, wireframe teal below it."""
    return _METER_ACTIVE_RGBA if score >= threshold else _WIREFRAME_RGBA


def flash_label(event_name: str) -> str:
    """Toast text for a gesture event value; unknown events are uppercased."""
    return FLASH_LABELS.get(event_name, str(event_name).upper())


class UpdateThrottle:
    """Drop events arriving faster than max_hz. Pure bookkeeping — the clock
    is injectable so tests never sleep."""

    def __init__(self, max_hz: float) -> None:
        # Tiny tolerance so a stream at exactly max_hz isn't half-dropped by
        # float rounding of the timestamps.
        self._min_interval = (1.0 / float(max_hz)) * (1.0 - 1e-6)
        self._last: float | None = None

    def should_accept(self, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        if self._last is not None and (now - self._last) < self._min_interval:
            return False
        self._last = now
        return True


def _nscolor(rgba: tuple[float, float, float, float]) -> AppKit.NSColor:
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba)


class _LandmarkView(AppKit.NSView):
    """Scatter-draws the latest landmark array. Main thread only."""

    def initWithFrame_(self, frame):
        self = objc.super(_LandmarkView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._points = None
        return self

    def setPoints_(self, points) -> None:
        self._points = points
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect) -> None:
        if self._points is None or len(self._points) == 0:
            return
        bounds = self.bounds()
        w, h = bounds.size.width, bounds.size.height
        r = _DOT_RADIUS
        path = AppKit.NSBezierPath.bezierPath()
        for x, y in self._points:
            # Mirror x so it behaves like a mirror; flip y because MediaPipe
            # is top-down and Cocoa is bottom-up.
            px = (1.0 - float(x)) * w
            py = (1.0 - float(y)) * h
            path.appendBezierPathWithOvalInRect_(
                AppKit.NSMakeRect(px - r, py - r, 2 * r, 2 * r)
            )
        _nscolor(_WIREFRAME_RGBA).setFill()
        path.fill()


class HUD:
    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._pill: AppKit.NSView | None = None
        self._pill_label: AppKit.NSTextField | None = None
        self._canvas: _LandmarkView | None = None
        self._metrics_label: AppKit.NSTextField | None = None
        self._bar_track: AppKit.NSView | None = None
        self._bar_fill: AppKit.NSView | None = None
        self._instruction_label: AppKit.NSTextField | None = None
        # key -> (fill view, tick view) per gesture meter row
        self._meters: dict[str, tuple[AppKit.NSView, AppKit.NSView]] = {}
        self._flash_field: AppKit.NSTextField | None = None
        self._flash_timer = None
        self._throttle = UpdateThrottle(_LANDMARK_MAX_HZ)
        self._meter_throttle = UpdateThrottle(_GESTURE_MAX_HZ)

    # -- public, thread-safe ------------------------------------------------

    def show(self) -> None:
        AppHelper.callAfter(self._show_main)

    def hide(self) -> None:
        AppHelper.callAfter(self._hide_main)

    def set_status(self, mode: str) -> None:
        AppHelper.callAfter(self._status_main, str(mode))

    def update_landmarks(self, points) -> None:
        """points: (N, 2) array of normalized [0,1] x,y face landmarks.
        Updates above 30Hz are dropped on the caller's thread."""
        if not self._throttle.should_accept():
            return
        pts = np.array(points, dtype=np.float64).reshape(-1, 2)  # own copy
        AppHelper.callAfter(self._landmarks_main, pts)

    def set_metrics(self, fps: float, latency_ms: float) -> None:
        AppHelper.callAfter(self._metrics_main, float(fps), float(latency_ms))

    def set_calibration_progress(self, fraction: float | None) -> None:
        AppHelper.callAfter(
            self._progress_main, None if fraction is None else float(fraction)
        )

    def set_instruction(self, text: str | None) -> None:
        """Wizard guidance line over the wireframe; None hides it."""
        AppHelper.callAfter(self._instruction_main, text)

    def set_gesture_levels(self, levels: dict[str, tuple[float, float]]) -> None:
        """levels: gesture key (see GESTURE_METERS) -> (score, threshold),
        both 0..1 relative scores. Updates above ~15Hz are dropped on the
        caller's thread; unknown keys are ignored."""
        if not self._meter_throttle.should_accept():
            return
        snap = {  # own copy, plain floats — never share caller state
            str(k): (float(v[0]), float(v[1])) for k, v in levels.items()
        }
        AppHelper.callAfter(self._gesture_levels_main, snap)

    def flash_event(self, label: str) -> None:
        """Transient event toast over the wireframe ("left_click" → LEFT
        CLICK, ...); appears instantly, fades over ~0.6s. A new flash
        replaces any still-fading one."""
        AppHelper.callAfter(self._flash_main, str(label))

    # -- main thread only ---------------------------------------------------

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            return
        visible = AppKit.NSScreen.mainScreen().visibleFrame()
        rect = AppKit.NSMakeRect(
            visible.origin.x + visible.size.width - _WIDTH - _MARGIN,
            visible.origin.y + visible.size.height - _HEIGHT - _MARGIN,
            _WIDTH,
            _HEIGHT,
        )
        style = (
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)

        content = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, _WIDTH, _HEIGHT)
        )
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        content.layer().setCornerRadius_(14.0)

        # Status pill row
        pill = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(_MARGIN, _HEIGHT - _MARGIN - _PILL_H, _INNER_W, _PILL_H)
        )
        pill.setWantsLayer_(True)
        pill.layer().setCornerRadius_(_PILL_H / 2)
        content.addSubview_(pill)

        pill_label = AppKit.NSTextField.labelWithString_("")
        pill_label.setFrame_(AppKit.NSMakeRect(0, 4, _INNER_W, _PILL_H - 8))
        pill_label.setAlignment_(AppKit.NSTextAlignmentCenter)
        pill_label.setFont_(
            AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                11, AppKit.NSFontWeightSemibold
            )
        )
        pill.addSubview_(pill_label)

        # Calibration progress bar (thin, under the pill; hidden until driven)
        bar_y = _HEIGHT - _MARGIN - _PILL_H - 6 - _BAR_H
        track = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(_MARGIN, bar_y, _INNER_W, _BAR_H)
        )
        track.setWantsLayer_(True)
        track.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.12).CGColor()
        )
        track.layer().setCornerRadius_(_BAR_H / 2)
        track.setHidden_(True)
        content.addSubview_(track)

        fill = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, 0, _BAR_H)
        )
        fill.setWantsLayer_(True)
        fill.layer().setBackgroundColor_(_nscolor(_WIREFRAME_RGBA).CGColor())
        fill.layer().setCornerRadius_(_BAR_H / 2)
        track.addSubview_(fill)

        # Face wireframe canvas (sits above the gesture meter block)
        canvas_y = _METER_BASE_Y + _METER_BLOCK_H + 8
        canvas = _LandmarkView.alloc().initWithFrame_(
            AppKit.NSMakeRect(_MARGIN, canvas_y, _INNER_W, bar_y - canvas_y - 8)
        )
        canvas.setWantsLayer_(True)
        canvas.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.35).CGColor()
        )
        canvas.layer().setCornerRadius_(8.0)
        content.addSubview_(canvas)

        # Event toast, centered over the canvas (hidden until flashed)
        canvas_frame = canvas.frame()
        flash = AppKit.NSTextField.labelWithString_("")
        flash.setFrame_(
            AppKit.NSMakeRect(
                _MARGIN,
                canvas_frame.origin.y + canvas_frame.size.height / 2 - 12,
                _INNER_W,
                24,
            )
        )
        flash.setAlignment_(AppKit.NSTextAlignmentCenter)
        flash.setFont_(
            AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                16, AppKit.NSFontWeightBold
            )
        )
        flash.setTextColor_(AppKit.NSColor.whiteColor())
        flash.setWantsLayer_(True)
        flash.setHidden_(True)
        content.addSubview_(flash)

        # Gesture level meters, one row per gesture, top-to-bottom
        meters: dict[str, tuple[AppKit.NSView, AppKit.NSView]] = {}
        n = len(GESTURE_METERS)
        for i, (key, text) in enumerate(GESTURE_METERS):
            row_y = _METER_BASE_Y + (n - 1 - i) * _METER_ROW_H

            name = AppKit.NSTextField.labelWithString_(text)
            name.setFrame_(
                AppKit.NSMakeRect(
                    _MARGIN, row_y + 2, _METER_LABEL_W - 6, _METER_ROW_H - 4
                )
            )
            name.setFont_(
                AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                    8, AppKit.NSFontWeightMedium
                )
            )
            name.setTextColor_(
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.7, 1.0)
            )
            content.addSubview_(name)

            track_y = row_y + (_METER_ROW_H - _METER_TRACK_H) / 2
            mtrack = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(
                    _MARGIN + _METER_LABEL_W, track_y, _METER_TRACK_W, _METER_TRACK_H
                )
            )
            mtrack.setWantsLayer_(True)
            mtrack.layer().setBackgroundColor_(
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.12).CGColor()
            )
            mtrack.layer().setCornerRadius_(_METER_TRACK_H / 2)
            content.addSubview_(mtrack)

            mfill = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(0, 0, 0, _METER_TRACK_H)
            )
            mfill.setWantsLayer_(True)
            mfill.layer().setBackgroundColor_(_nscolor(_WIREFRAME_RGBA).CGColor())
            mfill.layer().setCornerRadius_(_METER_TRACK_H / 2)
            mtrack.addSubview_(mfill)

            # Threshold tick — sibling of the track so it can overhang it
            tick = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(
                    _MARGIN + _METER_LABEL_W - _METER_TICK_W / 2,
                    row_y + (_METER_ROW_H - _METER_TICK_H) / 2,
                    _METER_TICK_W,
                    _METER_TICK_H,
                )
            )
            tick.setWantsLayer_(True)
            tick.layer().setBackgroundColor_(
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.85, 0.9).CGColor()
            )
            content.addSubview_(tick)

            meters[key] = (mfill, tick)

        # Metrics line
        metrics = AppKit.NSTextField.labelWithString_("")
        metrics.setFrame_(AppKit.NSMakeRect(_MARGIN, 10, _INNER_W, 16))
        metrics.setAlignment_(AppKit.NSTextAlignmentCenter)
        metrics.setFont_(
            AppKit.NSFont.monospacedSystemFontOfSize_weight_(
                10, AppKit.NSFontWeightRegular
            )
        )
        metrics.setTextColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.7, 1.0)
        )
        content.addSubview_(metrics)

        # Wizard instruction line (over the top of the canvas; hidden until set)
        instruction = AppKit.NSTextField.wrappingLabelWithString_("")
        instruction.setFrame_(
            AppKit.NSMakeRect(_MARGIN + 6, bar_y - 8 - 34, _INNER_W - 12, 30)
        )
        instruction.setAlignment_(AppKit.NSTextAlignmentCenter)
        instruction.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        instruction.setTextColor_(AppKit.NSColor.whiteColor())
        instruction.setHidden_(True)
        content.addSubview_(instruction)

        panel.setContentView_(content)
        self._panel = panel
        self._instruction_label = instruction
        self._pill, self._pill_label = pill, pill_label
        self._bar_track, self._bar_fill = track, fill
        self._canvas, self._metrics_label = canvas, metrics
        self._meters, self._flash_field = meters, flash
        self._status_main("idle")

    def _show_main(self) -> None:
        self._ensure_panel()
        self._panel.orderFrontRegardless()

    def _hide_main(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)

    def _status_main(self, mode: str) -> None:
        self._ensure_panel()
        accent = status_color(mode)
        tinted = (accent[0], accent[1], accent[2], 0.18)
        self._pill.layer().setBackgroundColor_(_nscolor(tinted).CGColor())
        self._pill_label.setStringValue_(status_label(mode))
        self._pill_label.setTextColor_(_nscolor(accent))

    def _landmarks_main(self, pts: np.ndarray) -> None:
        self._ensure_panel()
        self._canvas.setPoints_(pts)

    def _metrics_main(self, fps: float, latency_ms: float) -> None:
        self._ensure_panel()
        self._metrics_label.setStringValue_(format_metrics(fps, latency_ms))

    def _instruction_main(self, text: str | None) -> None:
        self._ensure_panel()
        self._instruction_label.setHidden_(text is None)
        self._instruction_label.setStringValue_(text or "")

    def _progress_main(self, fraction: float | None) -> None:
        self._ensure_panel()
        if fraction is None:
            self._bar_track.setHidden_(True)
            return
        clamped = max(0.0, min(1.0, fraction))
        self._bar_fill.setFrame_(
            AppKit.NSMakeRect(0, 0, clamped * _INNER_W, _BAR_H)
        )
        self._bar_track.setHidden_(False)

    def _gesture_levels_main(self, levels: dict[str, tuple[float, float]]) -> None:
        self._ensure_panel()
        for key, (score, threshold) in levels.items():
            meter = self._meters.get(key)
            if meter is None:
                continue
            mfill, tick = meter
            s, t = clamp01(score), clamp01(threshold)
            mfill.setFrame_(
                AppKit.NSMakeRect(0, 0, s * _METER_TRACK_W, _METER_TRACK_H)
            )
            mfill.layer().setBackgroundColor_(
                _nscolor(meter_fill_color(s, t)).CGColor()
            )
            tick.setFrameOrigin_(
                AppKit.NSMakePoint(
                    _MARGIN + _METER_LABEL_W + t * _METER_TRACK_W - _METER_TICK_W / 2,
                    tick.frame().origin.y,
                )
            )

    def _flash_main(self, label: str) -> None:
        self._ensure_panel()
        if self._flash_timer is not None:
            self._flash_timer.invalidate()
            self._flash_timer = None
        field = self._flash_field
        field.setStringValue_(flash_label(label))
        field.setAlphaValue_(1.0)
        field.setHidden_(False)
        start = time.monotonic()

        def _step(timer) -> None:
            # Replaced by a newer flash — that flash's timer owns the field.
            if self._flash_timer is not timer:
                timer.invalidate()
                return
            frac = (time.monotonic() - start) / _FLASH_DURATION_S
            if frac >= 1.0:
                timer.invalidate()
                self._flash_timer = None
                field.setAlphaValue_(0.0)
                field.setHidden_(True)
                return
            field.setAlphaValue_(1.0 - frac)

        # Repeating timer stepping alpha at ~30Hz; never blocks the main
        # thread (scheduled on the main run loop, fired between events).
        self._flash_timer = (
            AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
                _FLASH_STEP_S, True, _step
            )
        )
