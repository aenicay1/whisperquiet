"""MODERATE finding: if both the incremental finalize AND the whole-buffer
fallback decode raise, the daemon worker used to die mid-_stream_loop with
neither call wrapped — leaving "Status: finishing…" and the notch stuck on
"working…" forever (fix #4 in app.py's _stream_loop). This guards that such a
double decode failure is caught, logged, surfaced on the notch, and leaves
status/notch reset so the next dictation isn't blocked.
"""
from __future__ import annotations

import threading
import types

import numpy as np

import whisperquiet.app as appmod
from whisperquiet import incremental as incremental_mod


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


class _Stats:
    def __init__(self):
        self.calls = []

    def record(self, *args):
        self.calls.append(args)


class _FakeRecorder:
    """Mic never actually opens; stop() hands back a fixed non-silent buffer
    so _stream_loop takes the real decode path instead of the near-silence
    shortcut."""

    def __init__(self, audio):
        self._audio = audio

    def start(self):
        pass

    def stop(self, close_timeout: float = 2.0):
        return self._audio

    def snapshot(self):
        return self._audio

    def recent(self, seconds: float = 0.05):
        return self._audio

    def level(self):
        return 0.0


class _DoubleFailBackend:
    """Both the incremental finalize (via the fake IncrementalTranscriber
    below) and this whole-buffer fallback raise, matching the double-failure
    the fix guards against."""

    def transcribe(self, chunk, model_repo, language, vocabulary=None):
        return ""

    def transcribe_long(self, audio, model_repo, language, vocabulary=None):
        raise RuntimeError("decode boom")


class _FakeIncFinalizeFails:
    def __init__(self, tx_fn):
        self._tx = tx_fn

    def update(self, full_audio):
        return ""

    def finalize(self, full_audio):
        raise RuntimeError("finalize boom")


def _bare_app_for_stream_loop() -> appmod.WhisperQuietApp:
    app = appmod.WhisperQuietApp.__new__(appmod.WhisperQuietApp)
    app._recording = threading.Event()  # cleared: the record loop is skipped
    app._mic_ready = threading.Event()
    app._tx_lock = threading.Lock()
    app._release_t = 123.0  # a stale stamp the failure path must clear
    app._model_ready = threading.Event()
    app._model_unload_timer = None
    app._model_last_used_t = appmod.time.monotonic()
    app.indicator = _Indicator()
    app.status_item = _Status()
    app.stats = _Stats()
    app.config = types.SimpleNamespace(
        language=None,
        vocabulary=[],
        stream_interval=0.1,
        ptt_key="fn",
        mlx_clear_cache_after_decode=True,
        model_idle_unload_s=300.0,
    )
    app.recorder = _FakeRecorder(np.ones(4000, dtype=np.float32) * 0.1)
    return app


def test_double_decode_failure_resets_status_and_notifies(monkeypatch):
    monkeypatch.setattr(appmod.threading, "Timer", _NoOpTimer)
    monkeypatch.setattr(
        appmod.backends, "get_backend", lambda cfg: (_DoubleFailBackend(), "fake/model")
    )
    monkeypatch.setattr(incremental_mod, "IncrementalTranscriber", _FakeIncFinalizeFails)

    app = _bare_app_for_stream_loop()

    app._stream_loop()  # must not raise / must not hang

    assert app._release_t is None, "must not leave a stale release timestamp"
    assert app.status_item.title == "Status: idle (hold fn to talk)", (
        "must reset status instead of leaving 'Status: finishing…' stuck"
    )
    assert any(
        c[0] == "notify" and "transcription failed" in c[2] for c in app.indicator.calls
    ), "must surface the failure instead of leaving the notch stuck on working"
