"""Tests for MicRecorder robustness: the accepting + stream-generation guards
and the time-bounded stop() that stops a wedged audio device from hanging the
dictation worker (the bug that silently bricked all later push-to-talk presses).
"""
import time
import threading

import numpy as np
import pytest

import whisperquiet.audio as audio_mod
from whisperquiet.audio import MicRecorder


def _frame(n: int) -> np.ndarray:
    return np.ones((n, 1), dtype=np.float32)


def _push(r: MicRecorder, n: int) -> None:
    """Invoke the recorder's current-generation callback the way PortAudio would."""
    r._make_callback(r._stream_gen)(_frame(n), n, None, None)


def test_callback_ignored_when_not_accepting():
    r = MicRecorder()
    r._stream_gen = 1
    r._accepting = False
    _push(r, 100)
    assert r.snapshot().size == 0  # dropped
    r._accepting = True
    _push(r, 100)
    assert r.snapshot().size == 100  # accepted


def test_callback_ignored_when_stream_superseded():
    r = MicRecorder()
    r._accepting = True
    r._stream_gen = 1
    cb_old = r._make_callback(1)
    cb_old(_frame(50), 50, None, None)
    assert r.snapshot().size == 50
    # a new stream opened -> generation bumped; the OLD (abandoned) stream's
    # callback must no longer write into the new recording's buffer
    r._stream_gen = 2
    cb_old(_frame(50), 50, None, None)
    assert r.snapshot().size == 50  # unchanged


def test_stop_times_out_on_blocking_close_and_keeps_audio():
    r = MicRecorder()
    r._accepting = True
    r._stream_gen = 1
    _push(r, 1600)

    class HangStream:
        def stop(self):
            time.sleep(10)  # simulates a wedged PortAudio device

        def close(self):
            time.sleep(10)

    r._stream = HangStream()
    t0 = time.perf_counter()
    audio = r.stop(close_timeout=0.2)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.5, "stop() must not block on a wedged close"
    assert audio.size == 1600, "captured audio is still returned"
    assert r._stream is None
    assert r._accepting is False


def test_stop_clean_close_returns_audio():
    r = MicRecorder()
    r._accepting = True
    r._stream_gen = 1
    _push(r, 800)
    calls = {"stop": False, "close": False}

    class CleanStream:
        def stop(self):
            calls["stop"] = True

        def close(self):
            calls["close"] = True

    r._stream = CleanStream()
    audio = r.stop(close_timeout=1.0)
    assert audio.size == 800
    assert calls["stop"] and calls["close"]
    assert r._stream is None
    assert r._accepting is False


def test_stop_with_no_stream_is_safe():
    r = MicRecorder()
    assert r.stop().size == 0  # no stream, no audio, no crash


class _FakeStream:
    def __init__(self):
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        pass


def test_start_opens_and_goes_live(monkeypatch):
    # start() is synchronous and run-to-completion (it is called from the worker
    # thread, never the run loop). A clean open commits the stream and goes live.
    made = []

    def fake_input_stream(**kw):
        s = _FakeStream()
        made.append(s)
        return s

    monkeypatch.setattr(audio_mod._sounddevice(), "InputStream", fake_input_stream)
    r = MicRecorder()
    r.start()
    assert r._accepting is True
    assert r._stream is made[0] and made[0].started


def test_start_propagates_open_failure_so_worker_can_handle_it(monkeypatch):
    # Both the 16k open and the native-rate fallback fail (on both the initial
    # try and the rescan retry) -> start() RAISES rather than hangs, so the
    # worker's except shows "mic failed" and bails. It must never go live.
    def boom(**kw):
        raise RuntimeError("device unavailable")

    monkeypatch.setattr(audio_mod._sounddevice(), "InputStream", boom)
    monkeypatch.setattr(
        audio_mod._sounddevice(), "query_devices",
        lambda kind=None: {"default_samplerate": 48000, "name": "Fake"},
    )
    monkeypatch.setattr(audio_mod._sounddevice(), "_terminate", lambda: None)
    monkeypatch.setattr(audio_mod._sounddevice(), "_initialize", lambda: None)
    r = MicRecorder()
    with pytest.raises(RuntimeError):
        r.start()
    assert r._accepting is False  # never went live


def test_start_times_out_blocking_open_and_resets_portaudio(monkeypatch):
    released = threading.Event()
    calls = []

    class BlockingStream:
        def start(self):
            released.wait(2.0)
            raise RuntimeError("open unblocked after reset")

    monkeypatch.setattr(
        audio_mod._sounddevice(),
        "query_devices",
        lambda kind=None: {"default_samplerate": 16000, "name": "Fake"},
    )
    monkeypatch.setattr(
        audio_mod._sounddevice(), "InputStream", lambda **kw: BlockingStream()
    )

    def terminate():
        calls.append("terminate")
        released.set()

    monkeypatch.setattr(audio_mod._sounddevice(), "_terminate", terminate)
    monkeypatch.setattr(
        audio_mod._sounddevice(), "_initialize", lambda: calls.append("initialize")
    )
    r = MicRecorder()

    t0 = time.perf_counter()
    with pytest.raises(TimeoutError):
        r.start(open_timeout=0.05)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0
    assert calls == ["terminate", "initialize"]
    assert r._accepting is False
    assert r._stream is None


def test_open_stream_captures_at_native_rate(monkeypatch):
    # Open at the device's native rate (not a forced 16k), since the 16k force
    # is what triggers the AUHAL -10851 wedge on a 44.1/48k device.
    opened = {}

    class FakeStream:
        def __init__(self, **kw):
            opened["rate"] = kw.get("samplerate")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        audio_mod._sounddevice(), "query_devices",
        lambda kind=None: {"default_samplerate": 48000.0, "name": "EarPods"},
    )
    monkeypatch.setattr(
        audio_mod._sounddevice(), "InputStream", lambda **kw: FakeStream(**kw)
    )
    r = MicRecorder()
    r._open_stream()
    assert opened["rate"] == 48000
    assert r._rate == 48000


def test_open_stream_falls_back_to_16k_when_native_open_fails(monkeypatch):
    # If the native-rate open fails, fall back to the canonical 16k (built-in
    # mics accept it) rather than leaving the recorder with no stream.
    tried = []

    class PickyStream:
        def __init__(self, **kw):
            rate = kw.get("samplerate")
            tried.append(rate)
            if rate != 16000:
                raise RuntimeError("device refused native rate")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        audio_mod._sounddevice(), "query_devices",
        lambda kind=None: {"default_samplerate": 48000.0, "name": "X"},
    )
    monkeypatch.setattr(
        audio_mod._sounddevice(), "InputStream", lambda **kw: PickyStream(**kw)
    )
    r = MicRecorder()
    r._open_stream()
    assert tried == [48000, 16000]  # native first, then the 16k last resort
    assert r._rate == 16000


def test_recent_resamples_native_rate_to_16k():
    # recent() must hand spectrum_bands 16k-rate audio regardless of capture
    # rate, so the frequency-band mapping stays correct.
    r = MicRecorder()
    r._accepting = True
    r._stream_gen = 1
    r._rate = 48000
    _push(r, 2400)  # 0.05s at 48kHz
    out = r.recent(0.05)
    assert out.size == 800  # 2400 * 16000/48000 — resampled down to 16kHz


def test_close_quietly_swallows_errors():
    # Shared teardown helper must never raise (stop()'s watchdog relies on it).
    class Boom:
        def stop(self):
            raise RuntimeError("stop blew up")

        def close(self):
            raise RuntimeError("close blew up")

    MicRecorder._close_quietly(Boom())  # no exception escapes
