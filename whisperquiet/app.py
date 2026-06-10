"""Menu bar app: hold PTT → stream into overlay → commit on release."""

from __future__ import annotations

import threading
import time

import rumps

from . import config as config_mod
from . import inject, transcribe
from .audio import MicRecorder
from .hotkey import PushToTalk
from .overlay import NotchIndicator, Overlay


class WhisperQuietApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("🤫", quit_button="Quit")
        self.config = config_mod.load()
        config_mod.save(self.config)  # write defaults on first run
        self.status_item = rumps.MenuItem("Status: loading model…")
        self.menu = [self.status_item, None]

        self.recorder = MicRecorder()
        self.overlay = Overlay()
        self.indicator = NotchIndicator()
        self._recording = threading.Event()
        self._worker: threading.Thread | None = None

        self.ptt = PushToTalk(
            self.config.ptt_key, self._on_ptt_press, self._on_ptt_release
        )
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _warm_up(self) -> None:
        transcribe.warm_up(self.config.model_repo)
        self.status_item.title = f"Status: idle (hold {self.config.ptt_key} to talk)"
        self.ptt.start()

    # -- PTT edges (called from the pynput listener thread) -----------------

    def _on_ptt_press(self) -> None:
        if self._recording.is_set():
            return
        self._recording.set()
        self.status_item.title = "Status: listening"
        self.recorder.start()
        self.overlay.show()
        self.indicator.show()
        self._worker = threading.Thread(target=self._stream_loop, daemon=True)
        self._worker.start()
        threading.Thread(target=self._level_loop, daemon=True).start()

    def _on_ptt_release(self) -> None:
        self._recording.clear()

    def _level_loop(self) -> None:
        while self._recording.is_set():
            self.indicator.set_level(self.recorder.level())
            time.sleep(0.08)

    # -- streaming worker ----------------------------------------------------

    def _stream_loop(self) -> None:
        cfg = self.config
        # Re-transcribe the growing buffer while the key is held. Naive but
        # fine for week 1; incremental decoding is a later optimization.
        while self._recording.is_set():
            partial = transcribe.transcribe(
                self.recorder.snapshot(), cfg.model_repo, cfg.language
            )
            if partial:
                self.overlay.update(partial)
            self._recording_wait(cfg.stream_interval)

        audio = self.recorder.stop()
        self.indicator.hide()
        self.status_item.title = "Status: finishing…"
        final = transcribe.transcribe(audio, cfg.model_repo, cfg.language)
        if final:
            self.overlay.update(final)
            inject.type_text(final, cfg.inject_mode)
        self.overlay.hide()
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
