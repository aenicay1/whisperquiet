"""Killable, process-isolated microphone capture.

CoreAudio/PortAudio occasionally blocks forever inside stream open or close
after a device hot-swap.  Python cannot stop a thread stuck in that native call,
and PortAudio state is process-global, so abandoning the thread only leaves the
app poisoned.  This module makes the process the recovery boundary: every
recording owns a small audio child, audio chunks stream back to the parent, and
the parent can terminate the child without losing the audio already captured.
The next push-to-talk therefore always starts with a fresh PortAudio process.
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import threading
import time
from multiprocessing.connection import Connection
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE


WorkerTarget = Callable[[Connection, Connection], None]


def _watch_parent(parent_pid: int) -> None:
    """Do not leave an audio child behind if the menu-bar app is force-killed."""
    while True:
        time.sleep(0.5)
        if os.getppid() != parent_pid:
            os._exit(0)


def _open_native_stream(sd, callback):
    """Open the current default input, preferring its native sample rate."""
    rate, name = SAMPLE_RATE, "?"
    try:
        info = sd.query_devices(kind="input")
        rate = int(info["default_samplerate"]) or SAMPLE_RATE
        name = str(info["name"])
    except Exception:
        pass

    rates = [rate]
    if rate != SAMPLE_RATE:
        rates.append(SAMPLE_RATE)
    last_error: Exception | None = None
    for candidate in rates:
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=candidate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            stream.start()
            return stream, candidate, name
        except Exception as exc:
            last_error = exc
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
    assert last_error is not None
    raise last_error


def _open_with_rescan(sd, callback):
    """Refresh PortAudio's device snapshot and retry one ordinary open error."""
    try:
        return _open_native_stream(sd, callback)
    except Exception:
        try:
            sd._terminate()
        except Exception:
            pass
        sd._initialize()
        return _open_native_stream(sd, callback)


def _audio_process_main(control: Connection, audio: Connection) -> None:
    """Child entry: own PortAudio, stream chunks, and obey a stop command."""
    parent_pid = os.getppid()
    threading.Thread(
        target=_watch_parent, args=(parent_pid,), daemon=True
    ).start()

    # Import only in the child. The always-alive app process never opens or
    # resets PortAudio, so a poisoned host API dies with this process.
    import sounddevice as sd

    pending: queue.Queue[bytes | None] = queue.Queue(maxsize=1024)
    dropped = 0

    def _sender() -> None:
        while True:
            packet = pending.get()
            try:
                if packet is None:
                    return
                audio.send_bytes(packet)
            except (BrokenPipeError, EOFError, OSError):
                return
            finally:
                pending.task_done()

    sender = threading.Thread(target=_sender, daemon=True)
    sender.start()

    def _callback(indata, frames, time_info, status) -> None:
        nonlocal dropped
        packet = np.asarray(indata[:, 0], dtype=np.float32).tobytes()
        try:
            pending.put_nowait(packet)
        except queue.Full:
            # The callback must never block CoreAudio. The parent drains every
            # ~50ms in normal use; this is only a final overload guard.
            dropped += 1

    stream = None
    ready = False
    try:
        stream, rate, name = _open_with_rescan(sd, _callback)
        control.send(("ready", rate, name))
        ready = True

        while True:
            command = control.recv()
            if command != "stop":
                continue
            # These native calls are allowed to block: the parent owns the
            # timeout and can SIGKILL this whole process safely.
            stream.stop()
            stream.close()
            stream = None
            pending.put(None)
            pending.join()
            sender.join()
            control.send(("stopped", dropped))
            return
    except (EOFError, BrokenPipeError):
        return
    except BaseException as exc:
        try:
            control.send(("error", type(exc).__name__, str(exc), ready))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        try:
            control.close()
        except OSError:
            pass
        try:
            audio.close()
        except OSError:
            pass


class ProcessMicRecorder:
    """MicRecorder-compatible supervisor around one killable audio child."""

    def __init__(
        self,
        *,
        context=None,
        worker_target: WorkerTarget = _audio_process_main,
    ) -> None:
        self._context = context or multiprocessing.get_context("spawn")
        self._worker_target = worker_target
        self._process = None
        self._control: Connection | None = None
        self._audio: Connection | None = None
        self._chunks: list[np.ndarray] = []
        self._rate = SAMPLE_RATE
        self._data_lock = threading.Lock()
        # ``multiprocessing.Connection`` has one framed byte stream, not a
        # broadcast channel. The dictation worker (snapshot) and the visual
        # meter (recent) both drain it, so they must never poll/recv together:
        # two readers can split a frame and leave one permanently blocked.
        self._audio_read_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()

    @property
    def child_alive(self) -> bool:
        process = self._process
        return bool(process is not None and process.is_alive())

    def start(self, open_timeout: float = 8.0) -> None:
        """Spawn the audio engine and wait a bounded time for a live stream."""
        timeout = max(0.01, float(open_timeout))
        with self._lifecycle_lock:
            self._shutdown_child(force=True)
            with self._data_lock:
                self._chunks = []
                self._rate = SAMPLE_RATE

            parent_control, child_control = self._context.Pipe(duplex=True)
            parent_audio, child_audio = self._context.Pipe(duplex=False)
            process = self._context.Process(
                target=self._worker_target,
                args=(child_control, child_audio),
                name="WhisperQuietAudio",
                daemon=True,
            )
            self._process = process
            self._control = parent_control
            self._audio = parent_audio
            try:
                process.start()
            except BaseException:
                self._shutdown_child(force=True)
                raise
            finally:
                child_control.close()
                child_audio.close()

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                message = self._recv_control(min(0.05, deadline - time.monotonic()))
                if message is not None:
                    kind = message[0]
                    if kind == "ready":
                        self._rate = int(message[1]) or SAMPLE_RATE
                        name = message[2]
                        if self._rate != SAMPLE_RATE:
                            print(
                                f"mic: capturing {self._rate}Hz ({name}) → 16kHz",
                                flush=True,
                            )
                        else:
                            print(f"mic: capturing 16000Hz ({name})", flush=True)
                        self._drain_audio()
                        return
                    if kind == "error":
                        detail = ": ".join(part for part in message[1:3] if part)
                        self._shutdown_child(force=True)
                        raise RuntimeError(detail or "mic failed to open")
                if not process.is_alive():
                    exitcode = process.exitcode
                    self._shutdown_child(force=False)
                    raise RuntimeError(f"audio process exited during open ({exitcode})")

            print("mic open timed out — killing audio process", flush=True)
            self._shutdown_child(force=True)
            raise TimeoutError("mic open timed out")

    def snapshot(self) -> np.ndarray:
        """All captured audio so far, mono float32 at 16kHz."""
        self._drain_audio()
        with self._data_lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)
            rate = self._rate
        return self._resample(audio, rate)

    def recent(self, seconds: float = 0.05) -> np.ndarray:
        """Recent captured audio at 16kHz for the live spectrum indicator."""
        self._drain_audio()
        with self._data_lock:
            rate = self._rate
            window = max(1, int(rate * max(0.0, seconds)))
            tail: list[np.ndarray] = []
            total = 0
            for chunk in reversed(self._chunks):
                tail.append(chunk)
                total += chunk.size
                if total >= window:
                    break
        if not tail:
            return np.zeros(0, dtype=np.float32)
        samples = np.concatenate(tail[::-1])[-window:]
        return self._resample(samples, rate)

    def level(self) -> float:
        samples = self.recent(0.15)
        if not samples.size:
            return 0.0
        rms = float(np.sqrt(np.mean(np.square(samples))))
        return min(1.0, rms / 0.04)

    def stop(self, close_timeout: float = 2.0) -> np.ndarray:
        """Stop capture; kill a wedged child and retain already-streamed audio."""
        timeout = max(0.01, float(close_timeout))
        with self._lifecycle_lock:
            process, control = self._process, self._control
            if process is None:
                return self.snapshot()
            try:
                if control is not None:
                    control.send("stop")
            except (BrokenPipeError, EOFError, OSError):
                pass

            stopped = False
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self._drain_audio()
                message = self._recv_control(min(0.02, deadline - time.monotonic()))
                if message is not None:
                    if message[0] == "stopped":
                        dropped = int(message[1]) if len(message) > 1 else 0
                        if dropped:
                            print(f"mic: dropped {dropped} overloaded audio chunks", flush=True)
                        stopped = True
                        break
                    if message[0] == "error":
                        break
                if not process.is_alive():
                    stopped = True
                    break

            self._drain_audio()
            if not stopped and process.is_alive():
                print("mic stop timed out — killing audio process", flush=True)
            self._shutdown_child(force=not stopped)
            return self.snapshot()

    def abandon_open(self) -> None:
        """Compatibility hook for the app's outer watchdog."""
        with self._lifecycle_lock:
            self._shutdown_child(force=True)

    def _recv_control(self, timeout: float):
        control = self._control
        if control is None:
            return None
        try:
            if control.poll(max(0.0, timeout)):
                return control.recv()
        except (BrokenPipeError, EOFError, OSError):
            return None
        return None

    def _drain_audio(self) -> None:
        chunks: list[np.ndarray] = []
        # Serialize poll + recv as one operation. Locking only the append below
        # is too late: recv_bytes itself consumes bytes from a shared framed
        # pipe, and concurrent reads can corrupt its framing or block forever.
        with self._audio_read_lock:
            connection = self._audio
            if connection is None:
                return
            try:
                while connection.poll():
                    packet = connection.recv_bytes()
                    if not packet:
                        continue
                    if len(packet) % np.dtype(np.float32).itemsize:
                        print(
                            f"mic: dropped corrupt audio packet ({len(packet)} bytes)",
                            flush=True,
                        )
                        continue
                    try:
                        chunks.append(np.frombuffer(packet, dtype=np.float32).copy())
                    except ValueError:
                        # A terminated child can leave a malformed payload. Do
                        # not let one packet kill the worker and lock out PTT.
                        print("mic: dropped unreadable audio packet", flush=True)
            except (EOFError, OSError):
                pass
        if chunks:
            with self._data_lock:
                self._chunks.extend(chunks)

    def _shutdown_child(self, *, force: bool) -> None:
        process = self._process
        if process is not None:
            if force and process.is_alive():
                process.terminate()
            process.join(0.35)
            if process.is_alive():
                process.kill()
                process.join(0.35)
        self._drain_audio()
        # Do not close the pipe underneath a concurrent drain. The receive
        # lock also makes child teardown deterministic after a failed take.
        with self._audio_read_lock:
            for connection in (self._control, self._audio):
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
        self._process = None
        self._control = None
        self._audio = None

    @staticmethod
    def _resample(audio: np.ndarray, rate: int) -> np.ndarray:
        if rate == SAMPLE_RATE or not audio.size:
            return audio.astype(np.float32, copy=False)
        n_out = max(1, int(audio.size * SAMPLE_RATE / rate))
        return np.interp(
            np.linspace(0, audio.size - 1, n_out),
            np.arange(audio.size),
            audio,
        ).astype(np.float32)


# Keep the app import terse while making the process boundary explicit here.
MicRecorder = ProcessMicRecorder
