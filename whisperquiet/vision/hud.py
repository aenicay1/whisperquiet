"""Camera-control heads-up display.

A non-activating NSPanel pinned to the top-right of the main screen: status
pill, live face-wireframe scatter, fps/latency line, and a calibration
progress bar. Same threading contract as overlay.py — all AppKit work is
marshalled to the main thread; public methods are safe to call from worker
threads.

The pure pieces (status→color table, label/metrics formatting, the landmark
update throttle) live at module level so they can be unit-tested headless.
"""

from __future__ import annotations

import time

import AppKit
import numpy as np
import objc
from PyObjCTools import AppHelper

_WIDTH, _HEIGHT, _MARGIN = 240, 300, 12
_PILL_H, _BAR_H = 24, 3
_INNER_W = _WIDTH - 2 * _MARGIN

_LANDMARK_MAX_HZ = 30.0
_DOT_RADIUS = 1.75

# Accent per status mode, RGBA in 0..1 (kept as plain tuples so the mapping
# is testable without AppKit).
STATUS_COLORS: dict[str, tuple[float, float, float, float]] = {
    "listening": (0.20, 0.84, 0.49, 1.0),  # green
    "scroll": (0.04, 0.52, 1.00, 1.0),  # blue
    "calibrating": (1.00, 0.62, 0.04, 1.0),  # orange
    "idle": (0.62, 0.62, 0.66, 1.0),  # gray
    "camera off": (0.62, 0.62, 0.66, 1.0),  # gray
}
_WIREFRAME_RGBA = (0.25, 0.87, 0.82, 0.9)  # cyan/teal


def status_color(mode: str) -> tuple[float, float, float, float]:
    """Accent RGBA for a status mode; unknown modes fall back to idle gray."""
    return STATUS_COLORS.get(mode, STATUS_COLORS["idle"])


def status_label(mode: str) -> str:
    return f"[{mode.upper()}]"


def format_metrics(fps: float, latency_ms: float) -> str:
    return f"{fps:.0f} fps · {latency_ms:.0f} ms"


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
        self._throttle = UpdateThrottle(_LANDMARK_MAX_HZ)

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

        # Face wireframe canvas
        canvas = _LandmarkView.alloc().initWithFrame_(
            AppKit.NSMakeRect(_MARGIN, 36, _INNER_W, bar_y - 36 - 8)
        )
        canvas.setWantsLayer_(True)
        canvas.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.35).CGColor()
        )
        canvas.layer().setCornerRadius_(8.0)
        content.addSubview_(canvas)

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

        panel.setContentView_(content)
        self._panel = panel
        self._pill, self._pill_label = pill, pill_label
        self._bar_track, self._bar_fill = track, fill
        self._canvas, self._metrics_label = canvas, metrics
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
