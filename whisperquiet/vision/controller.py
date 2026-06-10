"""Camera-control mode: glues FaceCapture → GestureEngine → mouse + HUD.

Lifecycle: start() shows the HUD and begins a ~1.5s neutral-face calibration
(averaged blendshapes become the engine baseline), then gestures go live.
All callbacks arrive on capture/mediapipe worker threads; HUD methods are
already thread-safe and mouse events are fire-and-forget Quartz posts.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from ..control import mouse
from ..control.gestures import GestureEngine, GestureEvent
from .capture import FaceCapture, FaceFrame
from .hud import HUD

CALIBRATION_FRAMES = 45  # ~1.5s at 30fps
SCROLL_LINES = 3
LANDMARK_STRIDE = 4  # 468 → 117 points for the wireframe
STATUS_REVERT_S = 0.6


class CameraController:
    def __init__(
        self,
        jaw_toggle_dictation: bool = False,
        on_toggle_dictation: Callable[[], None] | None = None,
    ) -> None:
        self._jaw_enabled = jaw_toggle_dictation
        self._on_toggle_dictation = on_toggle_dictation
        self.hud = HUD()
        self.engine = GestureEngine(self._on_event)
        self.capture = FaceCapture(self._on_frame)
        self.active = False
        self._calibration: list[dict[str, float]] = []
        self._calibrated = False
        self._frame_count = 0
        self._last_scroll_t = 0.0

    def start(self) -> None:
        self._calibration, self._calibrated = [], False
        self._frame_count = 0
        self.hud.show()
        self.hud.set_status("calibrating")
        self.hud.set_calibration_progress(0.0)
        self.capture.start()
        self.active = True

    def stop(self) -> None:
        self.active = False
        self.capture.stop()
        self.hud.set_status("camera off")
        self.hud.hide()

    # -- capture thread ------------------------------------------------------

    def _on_frame(self, frame: FaceFrame) -> None:
        self._frame_count += 1
        self.hud.update_landmarks(frame.landmarks[::LANDMARK_STRIDE, :2])
        if self._frame_count % 15 == 0:
            self.hud.set_metrics(frame.fps, frame.latency_ms)

        if not self._calibrated:
            self._calibration.append(frame.blendshapes)
            self.hud.set_calibration_progress(
                len(self._calibration) / CALIBRATION_FRAMES
            )
            if len(self._calibration) >= CALIBRATION_FRAMES:
                keys = self._calibration[-1].keys()
                baseline = {
                    k: float(np.mean([c.get(k, 0.0) for c in self._calibration]))
                    for k in keys
                }
                self.engine.set_baseline(baseline)
                self._calibrated = True
                self.hud.set_calibration_progress(None)
                self.hud.set_status("idle")
            return

        self.engine.process(frame.blendshapes, frame.timestamp_ms / 1000.0)
        if (
            self._last_scroll_t
            and time.monotonic() - self._last_scroll_t > STATUS_REVERT_S
        ):
            self._last_scroll_t = 0.0
            self.hud.set_status("idle")

    # -- gesture events (capture thread) --------------------------------------

    def _on_event(self, event: GestureEvent) -> None:
        if event is GestureEvent.LEFT_CLICK:
            mouse.click("left")
        elif event is GestureEvent.RIGHT_CLICK:
            mouse.click("right")
        elif event is GestureEvent.SCROLL_UP:
            mouse.scroll(SCROLL_LINES)
            self._mark_scrolling()
        elif event is GestureEvent.SCROLL_DOWN:
            mouse.scroll(-SCROLL_LINES)
            self._mark_scrolling()
        elif event is GestureEvent.TOGGLE_DICTATION:
            if self._jaw_enabled and self._on_toggle_dictation is not None:
                self._on_toggle_dictation()

    def _mark_scrolling(self) -> None:
        if not self._last_scroll_t:
            self.hud.set_status("scroll")
        self._last_scroll_t = time.monotonic()
