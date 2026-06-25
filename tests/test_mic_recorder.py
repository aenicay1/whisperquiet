"""Tests for MicRecorder robustness: the accepting + stream-generation guards
and the time-bounded stop() that stops a wedged audio device from hanging the
dictation worker (the bug that silently bricked all later push-to-talk presses).
"""
import time

import numpy as np

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
