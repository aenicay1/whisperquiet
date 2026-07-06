"""The hotkey goes live before the whisper model finishes loading (so the first
run isn't a dead keyboard during the model download). These guard that a press
BEFORE the model is ready surfaces "loading model…" instead of starting a
dictation worker that would stall on a cold decode — and that once the model is
ready, a press records normally.
"""
import threading
import types

import whisperquiet.app as appmod
from whisperquiet import config as config_mod


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


class _Stats:
    def __init__(self):
        self.calls = []

    def record(self, *args):
        self.calls.append(args)


class _NoOpTimer:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def cancel(self):
        pass


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        if self.target is not None:
            self.target(*self.args, **self.kwargs)

    def is_alive(self):
        return False


def _bare_app():
    # bypass __init__ (it builds a rumps.App + starts threads); wire only what
    # _on_ptt_press touches.
    app = appmod.WhisperQuietApp.__new__(appmod.WhisperQuietApp)
    app._recording = threading.Event()
    app._model_ready = threading.Event()
    app._model_load_error = None
    app._worker = None
    app._tx_lock = threading.Lock()
    app._model_last_used_t = appmod.time.monotonic()
    app._model_unload_timer = None
    app._model_loading = threading.Event()
    app.config = types.SimpleNamespace(
        dictation_backend="whisper",
        model_repo=config_mod.LIGHT_MODEL_REPO,
        model_profile="light",
        parakeet_repo="org/parakeet",
        ptt_key="alt_r",
        model_idle_unload_s=300.0,
        mlx_clear_cache_after_decode=True,
        mlx_cache_limit_mb=256,
        mlx_memory_limit_mb=0,
        cleanup_enabled=True,
        keep_audio=True,
        keep_transcripts=True,
        inject_mode="keystrokes",
        stream_interval=0.7,
        audio_retention_mb=512,
        audio_retention_days=30,
        vocabulary=[],
    )
    app.indicator = _Indicator()
    app.status_item = _Status()
    app.stats = _Stats()
    return app


def test_press_before_model_ready_does_not_start_a_dictation(monkeypatch):
    # stub the auto-hide timer so nothing real spawns; the guard must return
    # before any worker is created.
    monkeypatch.setattr(appmod.threading, "Timer", _NoOpTimer)
    app = _bare_app()  # _model_ready intentionally clear
    monkeypatch.setattr(app, "_model_is_cached", lambda _repo: False)

    app._on_ptt_press()

    assert not app._recording.is_set(), "must not arm recording before model ready"
    assert app._worker is None, "must not start a dictation worker"
    assert any(c[0] == "notify" for c in app.indicator.calls), "must tell the user"


def test_press_before_model_ready_records_when_model_is_cached(monkeypatch):
    app = _bare_app()  # _model_ready intentionally clear
    monkeypatch.setattr(app, "_model_is_cached", lambda _repo: True)
    monkeypatch.setattr(app, "_stream_loop", lambda: None)
    monkeypatch.setattr(app, "_level_loop", lambda: None)

    app._on_ptt_press()

    assert app._recording.is_set(), "cached local model should cold-load in worker"
    assert app._worker is not None, "the dictation worker should be spawned"
    assert ("listening",) in app.indicator.calls
    assert app.status_item.title == "Status: listening (loading model)"


def test_press_during_active_model_load_does_not_start_second_load(monkeypatch):
    monkeypatch.setattr(appmod.threading, "Timer", _NoOpTimer)
    app = _bare_app()
    app._model_loading.set()
    monkeypatch.setattr(app, "_model_is_cached", lambda _repo: True)

    app._on_ptt_press()

    assert not app._recording.is_set()
    assert app._worker is None
    assert ("notify", "...", "loading model…") in app.indicator.calls


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


def test_idle_unload_drops_model_and_clears_ready(monkeypatch):
    app = _bare_app()
    app._model_ready.set()
    app._model_last_used_t = appmod.time.monotonic() - 301.0
    reclaimed = []

    class Backend:
        calls = []

        def unload_model(self, model_repo):
            self.calls.append(model_repo)
            return True

    backend = Backend()
    monkeypatch.setattr(
        appmod.backends,
        "get_backend",
        lambda _config: (backend, config_mod.LIGHT_MODEL_REPO),
    )
    monkeypatch.setattr(
        app,
        "_reclaim_mlx_memory",
        lambda label: reclaimed.append(label),
    )

    app._unload_model_if_idle()

    assert backend.calls == [config_mod.LIGHT_MODEL_REPO]
    assert reclaimed == ["after idle unload"]
    assert not app._model_ready.is_set()
    assert ("model_idle_unload",) in app.stats.calls
    assert app.status_item.title == "Status: idle (cold, hold alt_r to talk)"


# -- fix #1: model download/load failure must not hang forever silently -----


class _FailingBackend:
    """Stands in for whisperquiet.transcribe/parakeet: warm_up always raises,
    like a dead network connection or a full disk during the first-run
    model download."""

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


def test_switch_model_profile_unloads_old_model_and_loads_selected_profile(monkeypatch):
    app = _bare_app()
    app._model_ready.set()
    unloaded = []
    saved = []
    reclaimed = []
    loaded = []

    class Backend:
        def unload_model(self, model_repo):
            unloaded.append(model_repo)
            return True

    monkeypatch.setattr(
        appmod.backends,
        "get_backend",
        lambda config: (Backend(), config.model_repo),
    )
    monkeypatch.setattr(appmod.config_mod, "save", lambda cfg: saved.append(cfg.model_repo))
    monkeypatch.setattr(app, "_reclaim_mlx_memory", reclaimed.append)
    monkeypatch.setattr(app, "_load_selected_model", lambda label: loaded.append(label))
    monkeypatch.setattr(appmod.threading, "Thread", _ImmediateThread)

    app._switch_model_profile("accuracy")

    assert unloaded == [config_mod.LIGHT_MODEL_REPO]
    assert app.config.model_profile == "accuracy"
    assert app.config.model_repo == config_mod.ACCURACY_MODEL_REPO
    assert saved == [config_mod.ACCURACY_MODEL_REPO]
    assert reclaimed == ["after model switch unload"]
    assert loaded == ["model switch"]
    assert not app._model_ready.is_set()


def test_apply_preferences_normalizes_dictionary_and_saves(monkeypatch):
    app = _bare_app()
    saved = []
    scheduled = []
    monkeypatch.setattr(appmod.config_mod, "save", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(app, "_schedule_model_idle_unload", lambda: scheduled.append(True))

    app._apply_preferences({"vocabulary": [" WQ ", "wq", "", 12, "Origent."]})

    assert app.config.vocabulary == ["WQ.", "Origent."]
    assert saved == [app.config]
    assert scheduled == [True]
