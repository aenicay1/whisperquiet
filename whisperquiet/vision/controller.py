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

from .. import inject
from ..control import mouse
from ..control.gestures import GestureEngine, GestureEvent
from ..control.head_cursor import HeadCursor
from ..control.head_gestures import NodShakeDetector
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
        stats=None,
        cursor_gain: float | None = None,
    ) -> None:
        self._stats = stats
        self._jaw_enabled = jaw_toggle_dictation
        self._on_toggle_dictation = on_toggle_dictation
        self._saved_calibration = saved_calibration or None
        self._on_calibrated = on_calibrated or (lambda persisted: None)
        self.hud = HUD()
        self.engine = GestureEngine(self._on_event)
        self.capture = FaceCapture(self._on_frame)
        from ..control.head_cursor import CursorConfig
        cursor_cfg = CursorConfig()
        if cursor_gain:
            cursor_cfg.gain_px = float(cursor_gain)
        self.head = HeadCursor(cursor_cfg)
        self._nodshake = NodShakeDetector()
        self.cursor_enabled = False
        self.paused = False
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

    def apply_tunables(self, values: dict) -> None:
        """Hot-apply numeric settings from the playground bridge."""
        head_map = {
            "cursor_gain": "gain_px",
            "cursor_deadzone": "deadzone",
            "precision_scale": "precision_scale",
        }
        # manual wink/scroll thresholds override the wizard's per-side values
        clears = {
            "wink_on": ("wink_on_left", "wink_on_right"),
            "wink_off": ("wink_off_left", "wink_off_right"),
            "scroll_on": ("brow_on", "pucker_on"),
            "scroll_off": ("brow_off", "pucker_off"),
        }
        for key, value in values.items():
            if key in head_map:
                setattr(self.head.config, head_map[key], float(value))
            elif hasattr(self.engine.config, key):
                setattr(self.engine.config, key, float(value))
                for cleared in clears.get(key, ()):
                    setattr(self.engine.config, cleared, None)

    def _idle_status(self) -> str:
        if self.paused:
            return "paused"
        return "cursor" if self.cursor_enabled else "idle"

    def _record(self, kind: str) -> None:
        if self._stats is not None:
            self._stats.record(kind)

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
        nose = frame.landmarks[NOSE_TIP]
        if not self.paused:
            head_gesture = self._nodshake.process(float(nose[0]), float(nose[1]), t)
            if head_gesture == "nod":
                inject.press_key(inject.KEY_RETURN)
                self.hud.flash_event("ENTER")
                self._record("nod")
            elif head_gesture == "shake":
                inject.press_key(inject.KEY_ESCAPE)
                self.hud.flash_event("ESCAPE")
                self._record("shake")
        if self.cursor_enabled and not self.paused:
            self.head.set_precision(self.engine.winking)
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
        if event is GestureEvent.PAUSE_TOGGLE:
            self.paused = not self.paused
            if self.paused and self._dragging:
                mouse.left_up()
                self._dragging = False
            self.hud.flash_event("PAUSED" if self.paused else "RESUMED")
            self.hud.set_status(self._idle_status())
            self._record("pause")
            return
        if self.paused:
            return  # cheek puff is the only gesture that acts while paused
        self.hud.flash_event(flash_label(event.value))
        if event is GestureEvent.LEFT_CLICK:
            mouse.click("left")
            self._record("left_click")
        elif event is GestureEvent.DOUBLE_CLICK:
            mouse.double_click()
            self._record("double_click")
        elif event is GestureEvent.RIGHT_CLICK:
            mouse.click("right")
            self._record("right_click")
        elif event is GestureEvent.SCROLL_UP:
            mouse.scroll(SCROLL_LINES)
            self._mark_scrolling()
            self._record("scroll")
        elif event is GestureEvent.SCROLL_DOWN:
            mouse.scroll(-SCROLL_LINES)
            self._mark_scrolling()
            self._record("scroll")
        elif event is GestureEvent.DRAG_START:
            if self._jaw_enabled and self._on_toggle_dictation is not None:
                self._on_toggle_dictation()  # jaw repurposed as dictation toggle
            else:
                mouse.left_down()
                self._dragging = True
                self._record("drag")
        elif event is GestureEvent.DRAG_END:
            if self._dragging:
                mouse.left_up()
                self._dragging = False

    def _mark_scrolling(self) -> None:
        if not self._last_scroll_t:
            self.hud.set_status("scroll")
        self._last_scroll_t = time.monotonic()
