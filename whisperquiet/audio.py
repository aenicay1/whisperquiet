"""Microphone capture into a growing in-memory buffer while PTT is held."""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000  # what whisper expects


def peak_normalize(audio, target: float = 0.9):
    """Whispered speech is low-amplitude; scale peaks toward target so the
    model sees a healthy signal. No-op on silence."""
    import numpy as _np
    peak = float(_np.max(_np.abs(audio))) if audio.size else 0.0
    if peak < 1e-4:
        return audio
    return (audio * (target / peak)).astype(_np.float32)


def trim_trailing_silence(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    threshold: float = 2e-4,
    keep_s: float = 0.3,
) -> np.ndarray:
    """Drop trailing silence so whisper never decodes into dead air.

    Walks back from the end in ~50ms windows computing RMS and cuts everything
    after the last window whose RMS >= threshold, keeping an extra keep_s of
    padding (clamped to the array length). All-silent input is returned
    unchanged so the caller's existing silence guard still applies. Pure
    function: no state, never returns empty for non-silent input.
    """
    if audio.size == 0:
        return audio
    window = max(1, int(sample_rate * 0.05))
    pos = audio.size
    last_voiced_end = None
    while pos > 0:
        start = max(0, pos - window)
        rms = float(np.sqrt(np.mean(np.square(audio[start:pos], dtype=np.float64))))
        if rms >= threshold:
            last_voiced_end = pos
            break
        pos = start
    if last_voiced_end is None:
        return audio  # all silence: leave it to the caller's silence guard
    cut = min(audio.size, last_voiced_end + int(sample_rate * keep_s))
    return audio[:cut]


class MicRecorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()
            self._rate = SAMPLE_RATE
        except Exception:
            # some devices (AirPods etc.) refuse 16k; open at native rate
            # and resample in snapshot()
            info = sd.query_devices(kind="input")
            rate = int(info["default_samplerate"])
            self._stream = sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()
            self._rate = rate
            print(f"mic: 16k refused, using {rate}Hz ({info['name']})", flush=True)

    def _on_audio(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._chunks.append(indata.copy())

    def snapshot(self) -> np.ndarray:
        """All audio captured so far, mono float32 at 16kHz. Safe while recording."""
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)[:, 0]
        rate = getattr(self, "_rate", SAMPLE_RATE)
        if rate != SAMPLE_RATE and audio.size:
            n_out = int(audio.size * SAMPLE_RATE / rate)
            audio = np.interp(
                np.linspace(0, audio.size - 1, n_out),
                np.arange(audio.size),
                audio,
            ).astype(np.float32)
        return audio

    def level(self) -> float:
        """Mic level 0..1 over the last ~150ms, scaled for quiet speech."""
        window = int(SAMPLE_RATE * 0.15)
        with self._lock:
            tail: list[np.ndarray] = []
            total = 0
            for chunk in reversed(self._chunks):
                tail.append(chunk[:, 0])
                total += chunk.shape[0]
                if total >= window:
                    break
        if not tail:
            return 0.0
        samples = np.concatenate(tail[::-1])[-window:]
        rms = float(np.sqrt(np.mean(np.square(samples))))
        return min(1.0, rms / 0.04)

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return self.snapshot()
