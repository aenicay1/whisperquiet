"""Menu bar app: hold PTT → stream into overlay → commit on release."""

from __future__ import annotations

import threading
import time

import rumps

from . import config as config_mod
from . import inject, transcribe
from .audio import MicRecorder
from .stats import SessionStats
from .hotkey import PushToTalk
from .overlay import NotchIndicator, Overlay


class WhisperQuietApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("🤫", quit_button="Quit")
        self.config = config_mod.load()
        config_mod.save(self.config)  # write defaults on first run
        self.status_item = rumps.MenuItem("Status: loading model…")
        self.camera_item = rumps.MenuItem(
            "Camera Control (beta)", callback=self._toggle_camera
        )
        self.cursor_item = rumps.MenuItem(
            "Head Cursor", callback=self._toggle_cursor
        )
        self.menu = [self.status_item, self.camera_item, self.cursor_item, None]
        self._camera = None

        self.stats = SessionStats()
        self.recorder = MicRecorder()
        self.overlay = Overlay()
        self.indicator = NotchIndicator()
        self._recording = threading.Event()
        self._worker: threading.Thread | None = None

        self.ptt = PushToTalk(
            self.config.ptt_key, self._on_ptt_press, self._on_ptt_release
        )
        threading.Thread(target=self._warm_up, daemon=True).start()
        threading.Thread(target=self._watch_triggers, daemon=True).start()

    def _watch_triggers(self) -> None:
        """Out-of-band control: `touch <config dir>/trigger-camera` toggles
        camera control. Escape hatch for when the menu bar icon is hidden
        (notch overflow) or for scripting."""
        from PyObjCTools import AppHelper

        camera = config_mod.CONFIG_DIR / "trigger-camera"
        calibrate = config_mod.CONFIG_DIR / "trigger-calibrate"
        cursor = config_mod.CONFIG_DIR / "trigger-cursor"
        quit_file = config_mod.CONFIG_DIR / "trigger-quit"
        while True:
            if quit_file.exists():
                quit_file.unlink(missing_ok=True)
                AppHelper.callAfter(rumps.quit_application)
            if camera.exists():
                camera.unlink(missing_ok=True)
                AppHelper.callAfter(self._toggle_camera, self.camera_item)
            if calibrate.exists():
                calibrate.unlink(missing_ok=True)
                AppHelper.callAfter(self._recalibrate)
            if cursor.exists():
                cursor.unlink(missing_ok=True)
                AppHelper.callAfter(self._toggle_cursor, self.cursor_item)
            time.sleep(0.5)

    def _recalibrate(self) -> None:
        self.config.gestures.pop("calibration", None)
        config_mod.save(self.config)
        if self._camera is not None and self._camera.active:
            self._camera.recalibrate()
        else:
            if self._camera is not None:
                self._camera.recalibrate()  # drop stale saved thresholds
            self._toggle_camera(self.camera_item)

    def _warm_up(self) -> None:
        import Quartz
        from ApplicationServices import (
            AXIsProcessTrusted,
            AXIsProcessTrustedWithOptions,
        )

        trusted = AXIsProcessTrusted()
        print("accessibility trusted:", trusted, flush=True)
        if not trusted:  # pops the system dialog with an Open Settings button
            AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
        if hasattr(Quartz, "CGPreflightListenEventAccess"):
            if not Quartz.CGPreflightListenEventAccess():
                Quartz.CGRequestListenEventAccess()  # Input Monitoring prompt
        import AVFoundation as AV
        mic = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
        print("mic status:", mic, "(3=authorized)", flush=True)
        if mic == 0:
            AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AV.AVMediaTypeAudio, lambda granted: print("mic granted:", granted, flush=True)
            )
        transcribe.warm_up(self.config.model_repo)
        self.status_item.title = f"Status: idle (hold {self.config.ptt_key} to talk)"
        self.ptt.start()

    # -- PTT edges (called from the pynput listener thread) -----------------

    def _on_ptt_press(self) -> None:
        print("PTT press", flush=True)
        if self._recording.is_set():
            return
        if self._worker is not None and self._worker.is_alive():
            return  # previous session still finishing; drop this press
        self._recording.set()
        self.status_item.title = "Status: listening"
        self.recorder.start()
        self.overlay.show()
        self.indicator.show()
        self._worker = threading.Thread(target=self._stream_loop, daemon=True)
        self._worker.start()
        threading.Thread(target=self._level_loop, daemon=True).start()

    def _on_ptt_release(self) -> None:
        print("PTT release", flush=True)
        self._recording.clear()
        # instant feedback: UI drops now, final transcription finishes unseen
        self.indicator.hide()
        self.overlay.hide()

    def _toggle_camera(self, item: rumps.MenuItem) -> None:
        if self._camera is not None and self._camera.active:
            self._camera.stop()
            item.state = 0
            return
        if not self._ensure_camera_permission(item):
            return
        # deferred import: mediapipe/opencv load only if the mode is used
        from .vision.controller import CameraController

        if self._camera is None:
            self._camera = CameraController(
                jaw_toggle_dictation=bool(
                    self.config.gestures.get("jaw_toggle_dictation", False)
                ),
                on_toggle_dictation=self._toggle_dictation,
                saved_calibration=self.config.gestures.get("calibration"),
                on_calibrated=self._save_calibration,
                stats=self.stats,
            )
        self._camera.start()
        item.state = 1

    def _toggle_cursor(self, item: rumps.MenuItem) -> None:
        if self._camera is None or not self._camera.active:
            self._toggle_camera(self.camera_item)  # cursor needs the camera
        if self._camera is None or not self._camera.active:
            return  # camera blocked on permission; user retries after grant
        item.state = 1 if self._camera.toggle_cursor() else 0

    def _save_calibration(self, persisted: dict) -> None:
        self.config.gestures["calibration"] = persisted
        config_mod.save(self.config)

    def _ensure_camera_permission(self, item: rumps.MenuItem) -> bool:
        """TCC camera prompt must come from this app's run loop — bare CLI
        scripts get silently refused. Re-enters _toggle_camera on grant."""
        import AVFoundation as AV
        from PyObjCTools import AppHelper

        status = AV.AVCaptureDevice.authorizationStatusForMediaType_(
            AV.AVMediaTypeVideo
        )
        if status == 3:  # authorized
            return True
        if status == 0:  # not determined → prompt
            def handler(granted: bool) -> None:
                if granted:
                    AppHelper.callAfter(self._toggle_camera, item)
                else:
                    self.status_item.title = "Status: camera denied"
            AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                AV.AVMediaTypeVideo, handler
            )
            self.status_item.title = "Status: waiting for camera permission…"
        else:  # denied/restricted
            self.status_item.title = (
                "Status: camera denied — System Settings → Privacy → Camera"
            )
        return False

    def _toggle_dictation(self) -> None:
        if self._recording.is_set():
            self._on_ptt_release()
        else:
            self._on_ptt_press()

    def _level_loop(self) -> None:
        while self._recording.is_set():
            self.indicator.set_level(self.recorder.level())
            time.sleep(0.08)

    # -- streaming worker ----------------------------------------------------

    def _stream_loop(self) -> None:
        cfg = self.config
        # Re-transcribe the growing buffer while the key is held. Naive but
        # fine for week 1; incremental decoding is a later optimization.
        last_partial, last_size = "", -1
        while self._recording.is_set():
            snap = self.recorder.snapshot()
            partial = transcribe.transcribe(
                snap, cfg.model_repo, cfg.language, vocabulary=cfg.vocabulary
            )
            if partial:
                self.overlay.update(partial)
                last_partial, last_size = partial, snap.size
            self._recording_wait(cfg.stream_interval)

        audio = self.recorder.stop()
        self.status_item.title = "Status: finishing…"
        import numpy as np
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        if audio.size == last_size and last_partial:
            final = last_partial  # nothing new since the last partial
        elif rms < 2e-4:
            final = last_partial  # near-silence: don't let whisper hallucinate
        else:
            final = transcribe.transcribe(
                audio, cfg.model_repo, cfg.language, vocabulary=cfg.vocabulary
            )
        if final:
            inject.type_text(final, cfg.inject_mode)
            self.stats.record("dictation")
            self.stats.record("words", len(final.split()))
        self.status_item.title = f"Status: idle (hold {cfg.ptt_key} to talk)"

    def _recording_wait(self, seconds: float) -> None:
        # Sleep in small steps so release cuts the wait short
        step, waited = 0.05, 0.0
        while waited < seconds and self._recording.is_set():
            time.sleep(step)
            waited += step


def main() -> None:
    WhisperQuietApp().run()


if __name__ == "__main__":
    main()
