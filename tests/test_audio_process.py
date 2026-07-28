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


class _PacketConnection:
    """Minimal audio-pipe stand-in for packet decoding tests."""

    def __init__(self, *packets: bytes) -> None:
        self._packets = list(packets)

    def poll(self) -> bool:
        return bool(self._packets)

    def recv_bytes(self) -> bytes:
        return self._packets.pop(0)


def test_corrupt_audio_packet_is_dropped_and_later_packet_is_kept() -> None:
    recorder = ProcessMicRecorder(context=_spawn_context(), worker_target=_healthy_worker)
    good = np.array([0.25, -0.5], dtype=np.float32).tobytes()
    recorder._audio = _PacketConnection(b"bad", good)

    recorder._drain_audio()

    audio = recorder.snapshot()
    np.testing.assert_array_equal(audio, np.array([0.25, -0.5], dtype=np.float32))


class _ConcurrentReadConnection:
    """Raises if two callers race into one framed pipe read."""

    def __init__(self) -> None:
        self._packet = np.array([0.1, 0.2], dtype=np.float32).tobytes()
        self._available = True
        self._state_lock = threading.Lock()
        self._receive_lock = threading.Lock()
        self._poll_barrier = threading.Barrier(2)

    def poll(self) -> bool:
        with self._state_lock:
            available = self._available
        if not available:
            return False
        try:
            # With no recorder receive lock, both drains pass this barrier and
            # race into recv_bytes. With one, the first times out, drains, and
            # the second observes no packet.
            self._poll_barrier.wait(timeout=0.15)
        except threading.BrokenBarrierError:
            pass
        with self._state_lock:
            return self._available

    def recv_bytes(self) -> bytes:
        if not self._receive_lock.acquire(blocking=False):
            raise RuntimeError("concurrent audio-pipe read")
        try:
            time.sleep(0.01)
            with self._state_lock:
                if not self._available:
                    raise EOFError
                self._available = False
            return self._packet
        finally:
            self._receive_lock.release()


def test_concurrent_snapshot_and_meter_do_not_read_audio_pipe_together() -> None:
    recorder = ProcessMicRecorder(context=_spawn_context(), worker_target=_healthy_worker)
    recorder._audio = _ConcurrentReadConnection()
    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def drain() -> None:
        start.wait()
        try:
            recorder._drain_audio()
        except BaseException as exc:  # test captures a concurrent reader crash
            errors.append(exc)

    first = threading.Thread(target=drain)
    second = threading.Thread(target=drain)
    first.start()
    second.start()
    start.wait()
    first.join(0.75)
    second.join(0.75)

    assert not first.is_alive() and not second.is_alive()
    assert not errors
