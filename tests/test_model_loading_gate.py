"""The hotkey goes live before the whisper model finishes loading (so the first
run isn't a dead keyboard during the ~1.6 GB download). These guard that a press
BEFORE the model is ready surfaces "loading model…" instead of starting a
dictation worker that would stall on a cold decode — and that once the model is
ready, a press records normally.
"""
import threading

import whisperquiet.app as appmod


class _Indicator:
    def __init__(self):
        self.calls = []

    def notify(self, glyph, label):
        self.calls.append(("notify", glyph, label))

    def hide(self):
        self.calls.append(("hide",))

    def listening(self):
        self.calls.append(("listening",))

    def working(self):
        self.calls.append(("working",))


class _Status:
    title = ""


class _NoOpTimer:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass


def _bare_app():
    # bypass __init__ (it builds a rumps.App + starts threads); wire only what
    # _on_ptt_press touches.
    app = appmod.WhisperQuietApp.__new__(appmod.WhisperQuietApp)
    app._recording = threading.Event()
    app._model_ready = threading.Event()
    app._worker = None
    app.indicator = _Indicator()
    app.status_item = _Status()
    return app


def test_press_before_model_ready_does_not_start_a_dictation(monkeypatch):
    # stub the auto-hide timer so nothing real spawns; the guard must return
    # before any worker is created.
    monkeypatch.setattr(appmod.threading, "Timer", _NoOpTimer)
    app = _bare_app()  # _model_ready intentionally clear

    app._on_ptt_press()

    assert not app._recording.is_set(), "must not arm recording before model ready"
    assert app._worker is None, "must not start a dictation worker"
    assert any(c[0] == "notify" for c in app.indicator.calls), "must tell the user"


def test_press_after_model_ready_records(monkeypatch):
    app = _bare_app()
    app._model_ready.set()
    # neutralize the worker bodies so the press path runs without a real mic/decode
    monkeypatch.setattr(app, "_stream_loop", lambda: None)
    monkeypatch.setattr(app, "_level_loop", lambda: None)

    app._on_ptt_press()

    assert app._recording.is_set(), "press must arm recording once the model is ready"
    assert ("listening",) in app.indicator.calls
    assert app._worker is not None, "the dictation worker should be spawned"
