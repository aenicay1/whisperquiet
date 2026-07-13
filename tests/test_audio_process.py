"""Regression tests for process-isolated microphone recovery.

The production failure this locks down is a PortAudio/CoreAudio call that never
returns.  A Python thread cannot be killed, so recovery must terminate the
*process* that owns PortAudio and let the next push-to-talk spawn a clean one.
"""

from __future__ import annotations

import multiprocessing
import threading
import time

import numpy as np
import pytest

from whisperquiet.audio_process import ProcessMicRecorder


def _hang_on_open(control, audio) -> None:
    threading.Event().wait()


def _healthy_worker(control, audio) -> None:
    audio.send_bytes(np.ones(1600, dtype=np.float32).tobytes())
    control.send(("ready", 16000, "Fake Microphone"))
    if control.recv() == "stop":
        control.send(("stopped",))


def _hang_on_stop(control, audio) -> None:
    audio.send_bytes(np.ones(1600, dtype=np.float32).tobytes())
    control.send(("ready", 16000, "Wedged Microphone"))
    if control.recv() == "stop":
        threading.Event().wait()


def _native_rate_worker(control, audio) -> None:
    audio.send_bytes(np.ones(2400, dtype=np.float32).tobytes())
    control.send(("ready", 48000, "48k Microphone"))
    if control.recv() == "stop":
        control.send(("stopped",))


def _spawn_context():
    # Match the shipping app. Spawn is required once Cocoa/Metal/threads exist,
    # and the top-level synthetic targets above remain safely pickleable.
    return multiprocessing.get_context("spawn")


def test_hung_open_is_killed_and_next_recording_succeeds() -> None:
    recorder = ProcessMicRecorder(
        context=_spawn_context(), worker_target=_hang_on_open
    )

    started = time.perf_counter()
    with pytest.raises(TimeoutError, match="mic open timed out"):
        recorder.start(open_timeout=0.05)
    assert time.perf_counter() - started < 1.0
    assert not recorder.child_alive

    recorder._worker_target = _healthy_worker
    recorder.start(open_timeout=0.5)
    audio = recorder.stop(close_timeout=0.5)

    assert audio.size == 1600
    assert not recorder.child_alive


def test_hung_stop_is_killed_keeps_audio_and_next_recording_succeeds() -> None:
    recorder = ProcessMicRecorder(
        context=_spawn_context(), worker_target=_hang_on_stop
    )
    recorder.start(open_timeout=0.5)

    started = time.perf_counter()
    audio = recorder.stop(close_timeout=0.05)
    assert time.perf_counter() - started < 1.0
    assert audio.size == 1600
    assert not recorder.child_alive

    recorder._worker_target = _healthy_worker
    recorder.start(open_timeout=0.5)
    assert recorder.stop(close_timeout=0.5).size == 1600


def test_recent_audio_is_resampled_from_child_native_rate() -> None:
    recorder = ProcessMicRecorder(
        context=_spawn_context(), worker_target=_native_rate_worker
    )
    recorder.start(open_timeout=0.5)

    recent = recorder.recent(0.05)
    recorder.stop(close_timeout=0.5)

    assert recent.size == 800
