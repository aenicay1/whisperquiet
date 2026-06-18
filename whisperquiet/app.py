"""Menu bar app: hold PTT → stream into overlay → commit on release."""

from __future__ import annotations

import threading
import time

import rumps

from . import config as config_mod
from . import inject, transcribe
from .audio import MicRecorder
from .cleanup import clean as clean_text
from .feedback import FeedbackLog
from .settings_server import SettingsServer
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
        self.feedback = FeedbackLog()
        self.transcripts = FeedbackLog(config_mod.CONFIG_DIR / "transcripts.jsonl")
        self._last_commit_t = 0.0
        self._last_words = 0
        self._edit_keys = 0
        self._edit_logged = True
        self._last_phys_mouse = 0.0
        self.recorder = MicRecorder()
        self.overlay = Overlay()
        self.indicator = NotchIndicator()
        self._recording = threading.Event()
        self._worker: threading.Thread | None = None
        # serialize every mlx_whisper decode: the keep-warm thread must never
        # run a decode concurrently with a real dictation decode (shared model
        # arrays / default mlx stream are not safe under concurrent eval).
        self._tx_lock = threading.Lock()

        self.ptt = PushToTalk(
            self.config.ptt_key,
            self._on_ptt_press,
            self._on_ptt_release,
            flag_key_name=self.config.flag_key,
            on_flag=self._flag,
            on_physical_key=self._physical_key,
            on_physical_mouse=self._physical_mouse,
        )
        threading.Thread(target=self._warm_up, daemon=True).start()
        threading.Thread(target=self._watch_triggers, daemon=True).start()
        self.settings_server = SettingsServer(
            self._tunables_state, self._apply_tunables
        )
        print("settings bridge on port", self.settings_server.start(), flush=True)

    # (value, min, max, step, label, group) — schema for the playground tab
    _TUNABLES = {
        "cursor_gain": (3500, 1000, 8000, 100, "Cursor speed", "Cursor", "Pixels the cursor travels per unit of head movement. Higher = faster. Try 4000-5500 until crossing the screen feels effortless."),
        "cursor_deadzone": (0.0015, 0.0005, 0.005, 0.0001, "Cursor deadzone", "Cursor", "Head jitter smaller than this is ignored so the cursor sits still when you do. Raise if it trembles at rest; lower if small moves get eaten."),
        "precision_scale": (0.3, 0.1, 1.0, 0.05, "Aim slowdown factor", "Cursor", "Cursor speed multiplier while an eye is mid-wink (aiming). 0.3 = 70 percent slower for the landing. Set 1.0 to disable the slowdown."),
        "wink_on": (0.6, 0.2, 0.9, 0.01, "Wink trigger", "Winks", "How closed an eye must be to start a wink. LOWER this if winks get missed; raise it if squinting fires clicks. Overrides calibration."),
        "wink_off": (0.4, 0.1, 0.8, 0.01, "Wink release", "Winks", "How open the eye must be again to count as released - the click fires on release. Keep well below the trigger so winks do not stutter."),
        "wink_opposite_max": (0.3, 0.1, 0.8, 0.01, "Blink rejection", "Winks", "If the OTHER eye also closes past this, it is a natural blink and nothing fires. Raise if blinks cause clicks; lower if winks get rejected."),
        "double_wink_window": (0.6, 0.3, 1.2, 0.05, "Double-click window", "Winks", "Two left winks within this many seconds become a double-click. Raise if your double-winks land as two singles."),
        "scroll_on": (0.5, 0.2, 0.9, 0.01, "Scroll trigger", "Scrolling", "How strong the brow-raise (up) or pucker (down) must be to start scrolling. Lower = easier to trigger. Overrides calibration."),
        "scroll_off": (0.35, 0.1, 0.8, 0.01, "Scroll release", "Scrolling", "Relax below this to stop scrolling. The gap between trigger and release prevents flutter at the boundary."),
        "scroll_repeat": (0.15, 0.05, 0.4, 0.01, "Scroll start interval", "Scrolling", "Seconds between scroll steps when the gesture is first held. Lower = faster scrolling from the start."),
        "scroll_repeat_min": (0.05, 0.02, 0.2, 0.01, "Scroll max-speed interval", "Scrolling", "Top speed after about 2s of holding: one step per this many seconds. Lower = faster max; raise if you overshoot targets."),
        "jaw_on": (0.6, 0.3, 0.9, 0.01, "Drag trigger (jaw)", "Mouth", "How wide the mouth must open to grab for a drag. Lower if drags will not start; raise if talking causes accidental grabs."),
        "jaw_hold": (0.4, 0.2, 1.0, 0.05, "Drag hold time", "Mouth", "Seconds the mouth must stay open before the grab engages. Raise to filter accidental opens; lower for snappier drags."),
        "puff_on": (0.5, 0.3, 0.9, 0.01, "Pause trigger (puff)", "Mouth", "How strong a cheek puff must be to pause or resume all camera control. The panic switch - reachable but not hair-trigger."),
        "puff_hold": (0.3, 0.15, 1.0, 0.05, "Pause hold time", "Mouth", "Seconds the puff must be held to toggle pause. Raise if it toggles accidentally."),
        "stream_interval": (0.7, 0.3, 2.0, 0.1, "Partial update interval", "Dictation", "How often the live preview re-transcribes while you hold the talk key. Lower = snappier preview, more compute and battery."),
        "cleanup_enabled": (1, 0, 1, 1, "Dictation cleanup", "Dictation", "1 = remove fillers (um/uh), collapse repeats, resolve corrections like - no wait - before text lands. 0 = raw transcript."),
        "exp_head_cursor": (0, 0, 1, 1, "Head cursor (experimental)", "Experimental", "1 = head movement drives the pointer. Demoted in pivot #2: workable but the jankiest feature. Off keeps cursor toggles inert."),
        "exp_jaw_drag": (0, 0, 1, 1, "Jaw drag (experimental)", "Experimental", "1 = open-mouth-hold drags. Off by default: talking moves your jaw, so this fights dictation."),
        "exp_nod_shake": (0, 0, 1, 1, "Nod/shake keys (experimental)", "Experimental", "1 = quick nod presses Enter, head-shake presses Escape. Off by default: conversation head movement risks misfires."),
    }

    def _stored_tunables(self) -> dict:
        return self.config.gestures.get("tunables", {})

    def _tunables_state(self) -> dict:
        stored = dict(self._stored_tunables())
        for name, val in self.config.gestures.get("experimental", {}).items():
            stored.setdefault("exp_" + name, 1 if val else 0)
        stored.setdefault("cleanup_enabled", 1 if self.config.cleanup_enabled else 0)
        legacy_gain = self.config.gestures.get("cursor_gain")
        state = {}
        for key, (default, lo, hi, step, label, group, desc) in self._TUNABLES.items():
            value = stored.get(key, default)
            if key == "cursor_gain" and key not in stored and legacy_gain:
                value = legacy_gain
            state[key] = {
                "value": value, "min": lo, "max": hi, "step": step,
                "label": label, "group": group, "desc": desc,
            }
        return state

    def _apply_tunables(self, values: dict) -> None:
        clean = {
            k: float(v) for k, v in values.items()
            if k in self._TUNABLES and isinstance(v, (int, float))
        }
        if not clean:
            return
        self.config.gestures.setdefault("tunables", {}).update(clean)
        config_mod.save(self.config)
        if "stream_interval" in clean:
            self.config.stream_interval = clean["stream_interval"]
        if "cleanup_enabled" in clean:
            self.config.cleanup_enabled = bool(clean["cleanup_enabled"])
        exp = {
            key[4:]: bool(clean.pop(key))
            for key in list(clean)
            if key.startswith("exp_")
        }
        if exp:
            self.config.gestures.setdefault("experimental", {}).update(exp)
            config_mod.save(self.config)
        if self._camera is not None:
            if exp:
                self._camera.set_experimental(exp)
            self._camera.apply_tunables(clean)

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
        # keep the model resident: a tiny periodic decode every ~90s so the
        # first real dictation after idle isn't cold (mlx caches the model;
        # this keeps that cache warm without meaningful battery cost).
        threading.Thread(target=self._keep_warm, daemon=True).start()

    def _keep_warm(self) -> None:
        import numpy as np
        while True:
            time.sleep(90)
            # hold the decode lock so we can't overlap a dictation; re-check
            # _recording UNDER the lock to close the check-then-act race (a
            # press could land between the check and the decode otherwise).
            with self._tx_lock:
                if self._recording.is_set():
                    continue
                try:
                    transcribe.transcribe(
                        np.zeros(1600, dtype=np.float32), self.config.model_repo
                    )
                except Exception:
                    pass

    # -- dogfood feedback (called from the event tap, main thread) ----------

    def _flag(self) -> None:
        """User tapped the flag key right after something misbehaved."""
        self.feedback.log("flag", {"recent": self.stats.recent()})
        self.stats.record("flag")
        self.overlay.show()
        self.overlay.update("🚩 flagged")
        threading.Timer(0.9, self.overlay.hide).start()

    def _physical_key(self) -> None:
        if time.monotonic() - self._last_commit_t < 8.0:
            self._edit_keys += 1
            if self._edit_keys >= 4 and not self._edit_logged:
                self._edit_logged = True
                self.stats.record("dictation_edited")
                self.feedback.log(
                    "dictation_edited",
                    {"words": self._last_words, "keys": self._edit_keys},
                )

    def _physical_mouse(self) -> None:
        now = time.monotonic()
        camera_on = self._camera is not None and self._camera.active
        if camera_on and now - self._last_phys_mouse > 1.0:
            self.stats.record("trackpad_touch")
        self._last_phys_mouse = now

    # -- PTT edges (called from the pynput listener thread) -----------------

    def _on_ptt_press(self) -> None:
        print("PTT press", flush=True)
        if self._recording.is_set():
            return
        if self._worker is not None and self._worker.is_alive():
            return  # previous session still finishing; drop this press
        self._recording.set()
        self.status_item.title = "Status: listening"
        try:
            self.recorder.start()
        except Exception as exc:
            print("mic failed to open:", exc, flush=True)
            self._recording.clear()
            self.overlay.show()
            self.overlay.update("⚠️ mic failed — check input device")
            threading.Timer(2.0, self.overlay.hide).start()
            return
        self.indicator.show()  # notch "listening" pill is the only live cue
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
                cursor_gain=self.config.gestures.get("cursor_gain"),
                experimental=self.config.gestures.get("experimental"),
            )
        self._camera.start()
        self._camera.apply_tunables(self._stored_tunables())
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
        if self._camera is not None:  # user knobs outrank fresh wizard values
            self._camera.apply_tunables(self._stored_tunables())

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
        from .audio import spectrum_bands

        while self._recording.is_set():
            try:
                self.indicator.set_spectrum(spectrum_bands(self.recorder.recent()))
            except Exception:  # never let the meter break dictation
                self.indicator.set_level(self.recorder.level())  # fallback
            time.sleep(0.05)  # ~20Hz for smooth bars

    # -- streaming worker ----------------------------------------------------

    def _stream_loop(self) -> None:
        cfg = self.config
        import numpy as np
        from .incremental import IncrementalTranscriber

        # Incremental: each silence-bounded segment is transcribed ONCE and
        # locked, so only the live tail re-runs — constant release latency and
        # the preview equals the final. transcribe_fn binds model+vocab.
        def _tx(chunk):
            with self._tx_lock:  # never overlap the keep-warm decode
                return transcribe.transcribe(
                    chunk, cfg.model_repo, cfg.language, vocabulary=cfg.vocabulary
                )

        inc = IncrementalTranscriber(_tx)
        last_partial = ""
        use_incremental = True
        while self._recording.is_set():
            snap = self.recorder.snapshot()
            try:
                partial = inc.update(snap)
            except Exception as exc:  # fall back to whole-buffer, never break
                print("incremental update failed, falling back:", exc, flush=True)
                use_incremental = False
                with self._tx_lock:
                    partial = transcribe.transcribe(
                        snap, cfg.model_repo, cfg.language, vocabulary=cfg.vocabulary
                    )
            # incremental runs silently to pre-lock segments (fast release);
            # no live preview box — the notch indicator shows we're listening
            if partial:
                last_partial = partial
            self._recording_wait(cfg.stream_interval)

        audio = self.recorder.stop()
        self.status_item.title = "Status: finishing…"
        rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
        print(f"dictation: {audio.size/16000:.1f}s rms={rms:.5f}", flush=True)
        if rms < 2e-4:
            final = last_partial  # near-silence: don't let whisper hallucinate
        elif use_incremental:
            try:
                final = inc.finalize(audio)  # only the unfinalized tail re-runs
            except Exception as exc:
                print("incremental finalize failed, falling back:", exc, flush=True)
                with self._tx_lock:
                    final = transcribe.transcribe_long(
                        audio, cfg.model_repo, cfg.language, vocabulary=cfg.vocabulary
                    )
        else:
            with self._tx_lock:
                final = transcribe.transcribe_long(
                    audio, cfg.model_repo, cfg.language, vocabulary=cfg.vocabulary
                )
        audio_name = None
        if cfg.keep_audio and audio.size > 8000:
            try:
                import wave
                import numpy as np
                adir = config_mod.CONFIG_DIR / "audio"
                adir.mkdir(parents=True, exist_ok=True)
                audio_name = f"{int(time.time())}.wav"
                with wave.open(str(adir / audio_name), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes((audio * 32767).astype(np.int16).tobytes())
            except Exception:
                audio_name = None
        raw_final = final
        if final and cfg.cleanup_enabled:
            final = clean_text(final)
        if final and cfg.rescore_enabled:
            from .rescore import RescoreConfig, rescore
            final = rescore(final, RescoreConfig(enabled=True))
        if final and cfg.keep_transcripts and (raw_final != final or audio_name):
            self.transcripts.log(
                "pair", {"raw": raw_final, "clean": final, "audio": audio_name}
            )
        print(f"final: {len(final or '')} chars", flush=True)
        if not final and audio.size > 16000:
            self.overlay.show()
            self.overlay.update("…heard nothing — check mic/level bar")
            threading.Timer(1.5, self.overlay.hide).start()
        if final:
            inject.type_text(final, cfg.inject_mode)
            self.stats.record("dictation")
            self.stats.record("words", len(final.split()))
            self._last_commit_t = time.monotonic()
            self._last_words = len(final.split())
            self._edit_keys = 0
            self._edit_logged = False
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
