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

    def cancel(self):
        pass


def _bare_app():
    # bypass __init__ (it builds a rumps.App + starts threads); wire only what
    # _on_ptt_press touches.
    app = appmod.WhisperQuietApp.__new__(appmod.WhisperQuietApp)
    app._recording = threading.Event()
    app._model_ready = threading.Event()
    app._model_load_error = None
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


# -- fix #1: model download/load failure must not hang forever silently -----


class _FailingBackend:
    """Stands in for whisperquiet.transcribe/parakeet: warm_up always raises,
    like a dead network connection or a full disk during the first-run
    ~1.6 GB download."""

    def __init__(self):
        self.calls = 0

    def warm_up(self, model_repo):
        self.calls += 1
        raise ConnectionError("network down")


def test_warm_up_failure_sets_persistent_error_not_stuck_loading(monkeypatch):
    # skip the real backoff sleep between retries
    monkeypatch.setattr(appmod.time, "sleep", lambda *_: None)
    app = _bare_app()
    backend = _FailingBackend()

    ok = app._load_model_with_retries(backend, "org/model", attempts=2)

    assert ok is False
    assert backend.calls == 2, "should retry before giving up"
    assert not app._model_ready.is_set(), "must never claim readiness on failure"
    assert app._model_load_error, "must record a persistent, human-readable error"
    assert any(
        c[0] == "notify" and "download failed" in c[2] for c in app.indicator.calls
    ), "must surface the failure on the notch instead of leaving it on a spinner"
    assert "download failed" in app.status_item.title


def test_ptt_press_after_warm_up_failure_shows_error_not_loading(monkeypatch):
    monkeypatch.setattr(appmod.time, "sleep", lambda *_: None)
    app = _bare_app()
    app._load_model_with_retries(_FailingBackend(), "org/model", attempts=1)
    app.indicator.calls.clear()  # isolate what this press notifies

    app._on_ptt_press()

    assert not app._recording.is_set(), "must not start a dictation with no model"
    assert app._worker is None, "must not spawn a worker that would stall forever"
    notify_calls = [c for c in app.indicator.calls if c[0] == "notify"]
    assert notify_calls, "a press after a permanent failure must still notify"
    assert notify_calls[-1][2] == app._model_load_error
    assert "loading model" not in notify_calls[-1][2], (
        "must restate the real error, not the eternal 'loading model…' spinner"
    )
