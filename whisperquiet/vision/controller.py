"""Camera-control mode: glues FaceCapture → GestureEngine → mouse + HUD.

Lifecycle: start() shows the HUD; with no saved calibration it runs the
guided CalibrationWizard (per-gesture personal thresholds, persisted via
on_calibrated), otherwise it restores the saved thresholds and goes live
immediately. All callbacks arrive on capture/mediapipe worker threads; HUD
methods are already thread-safe and mouse events are fire-and-forget Quartz
posts.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ..control import mouse
from ..control.gestures import GestureEngine, GestureEvent
from ..control.head_cursor import HeadCursor
from .calibration import CalibrationWizard, config_from_saved
from .capture import FaceCapture, FaceFrame
from .hud import HUD, flash_label

NOSE_TIP = 1  # MediaPipe landmark index driving the cursor

SCROLL_LINES = 3
LANDMARK_STRIDE = 4  # 468 → 117 points for the wireframe
STATUS_REVERT_S = 0.6


class CameraController:
    def __init__(
        self,
        jaw_toggle_dictation: bool = False,
        on_toggle_dictation: Callable[[], None] | None = None,
        saved_calibration: dict | None = None,
        on_calibrated: Callable[[dict], None] | None = None,
    ) -> None:
        self._jaw_enabled = jaw_toggle_dictation
        self._on_toggle_dictation = on_toggle_dictation
        self._saved_calibration = saved_calibration or None
        self._on_calibrated = on_calibrated or (lambda persisted: None)
        self.hud = HUD()
        self.engine = GestureEngine(self._on_event)
        self.capture = FaceCapture(self._on_frame)
        self.head = HeadCursor()
        self.cursor_enabled = False
        self._dragging = False
        self.active = False
        self._wizard: CalibrationWizard | None = None
        self._calibrated = False
        self._frame_count = 0
        self._last_scroll_t = 0.0
        self._last_frame_t = 0.0
        self._last_restart_t = 0.0

    def start(self) -> None:
        self._frame_count = 0
        self.hud.show()
        if self._saved_calibration:
            config, baseline = config_from_saved(self._saved_calibration)
            self.engine = GestureEngine(self._on_event, config)
            self.engine.set_baseline(baseline)
            self._calibrated = True
            self.hud.set_status("idle")
        else:
            self._begin_wizard()
        self._last_frame_t = time.monotonic()
        self.capture.start()
        self.active = True
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _watchdog(self) -> None:
        """Restart the camera session if frames stop flowing (a stalled
        AVFoundation stream blocks cv2.VideoCapture.read forever)."""
        while self.active:
            time.sleep(2.0)
            stalled = time.monotonic() - self._last_frame_t > 4.0
            cooled = time.monotonic() - self._last_restart_t > 10.0
            if self.active and stalled and cooled:
                print("camera stalled — restarting capture", flush=True)
                self._last_restart_t = time.monotonic()
                self.hud.set_instruction("camera stalled — restarting…")
                try:
                    self.capture.stop()
                    self._last_frame_t = time.monotonic()
                    self.capture.start()
                except Exception as exc:
                    print("camera restart failed:", exc, flush=True)
                else:
                    self.hud.set_instruction(None)

    def toggle_cursor(self) -> bool:
        """Flip head-cursor mode; returns the new state."""
        self.cursor_enabled = not self.cursor_enabled
        if self.cursor_enabled:
            self.head.reset()
        elif self._dragging:
            mouse.left_up()
            self._dragging = False
        if self._calibrated:
            self.hud.set_status(self._idle_status())
        return self.cursor_enabled

    def _idle_status(self) -> str:
        return "cursor" if self.cursor_enabled else "idle"

    def stop(self) -> None:
        self.active = False
        if self._dragging:
            mouse.left_up()
            self._dragging = False
        self.capture.stop()
        self.hud.set_status("camera off")
        self.hud.set_instruction(None)
        self.hud.hide()

    def recalibrate(self) -> None:
        """Re-run the guided wizard (new lighting, glasses, different chair)."""
        self._saved_calibration = None
        if self.active:
            self._begin_wizard()

    def _begin_wizard(self) -> None:
        self._calibrated = False
        self._wizard = CalibrationWizard(on_instruction=self._on_instruction)
        self.hud.set_status("calibrating")
        self.hud.set_calibration_progress(0.0)

    def _on_instruction(self, text: str, fraction: float) -> None:
        self.hud.set_instruction(text)
        self.hud.set_calibration_progress(fraction)

    # -- capture thread ------------------------------------------------------

    def _on_frame(self, frame: FaceFrame) -> None:
        self._frame_count += 1
        self._last_frame_t = time.monotonic()
        if self._frame_count == 1:
            print("camera frames flowing", flush=True)
        self.hud.update_landmarks(frame.landmarks[::LANDMARK_STRIDE, :2])
        if self._frame_count % 15 == 0:
            self.hud.set_metrics(frame.fps, frame.latency_ms)

        if not self._calibrated:
            wizard = self._wizard
            if wizard is None:
                return
            wizard.process(frame.blendshapes, frame.timestamp_ms / 1000.0)
            if wizard.done:
                config, persisted = wizard.build()
                self.engine = GestureEngine(self._on_event, config)
                self.engine.set_baseline(wizard.baseline())
                self._wizard = None
                self._calibrated = True
                self.hud.set_calibration_progress(None)
                self.hud.set_instruction(None)
                self.hud.set_status(self._idle_status())
                self._on_calibrated(persisted)
            return

        t = frame.timestamp_ms / 1000.0
        self.engine.process(frame.blendshapes, t)
        self.hud.set_gesture_levels(self._gesture_levels(frame.blendshapes))
        if self.cursor_enabled:
            self.head.set_precision(self.engine.winking)
            nose = frame.landmarks[NOSE_TIP]
            delta = self.head.process(float(nose[0]), float(nose[1]), t)
            if delta is not None:
                mouse.move_by(*delta)
        if (
            self._last_scroll_t
            and time.monotonic() - self._last_scroll_t > STATUS_REVERT_S
        ):
            self._last_scroll_t = 0.0
            self.hud.set_status(self._idle_status())

    def _gesture_levels(self, blendshapes: dict) -> dict:
        """Live (score, threshold) pairs for the HUD meters, baseline-relative
        like the engine sees them."""
        cfg, base = self.engine.config, self.engine._baseline

        def rel(key: str) -> float:
            return max(0.0, blendshapes.get(key, 0.0) - base.get(key, 0.0))

        return {
            "l_wink": (rel("eyeBlinkLeft"), cfg.wink_on_left or cfg.wink_on),
            "r_wink": (rel("eyeBlinkRight"), cfg.wink_on_right or cfg.wink_on),
            "brow": (rel("browInnerUp"), cfg.brow_on or cfg.scroll_on),
            "pucker": (rel("mouthPucker"), cfg.pucker_on or cfg.scroll_on),
            "jaw": (rel("jawOpen"), cfg.jaw_on),
        }

    # -- gesture events (capture thread) --------------------------------------

    def _on_event(self, event: GestureEvent) -> None:
        self.hud.flash_event(flash_label(event.value))
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
        elif event is GestureEvent.DRAG_START:
            if self._jaw_enabled and self._on_toggle_dictation is not None:
                self._on_toggle_dictation()  # jaw repurposed as dictation toggle
            else:
                mouse.left_down()
                self._dragging = True
        elif event is GestureEvent.DRAG_END:
            if self._dragging:
                mouse.left_up()
                self._dragging = False

    def _mark_scrolling(self) -> None:
        if not self._last_scroll_t:
            self.hud.set_status("scroll")
        self._last_scroll_t = time.monotonic()
