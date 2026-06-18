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


def split_on_silence(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    min_silence_s: float = 0.6,
    silence_thresh: float = 2e-4,
    min_chunk_s: float = 0.4,
    max_chunk_s: float = 25.0,
) -> list[np.ndarray]:
    """Cut a long recording into speech spans at silent gaps.

    One-shot decoding of a long buffer drops whole middle sentences, so split
    the audio into short chunks the model can transcribe individually. Walks the
    signal in ~30ms RMS windows (same RMS as trim_trailing_silence): a run of
    windows below silence_thresh lasting >= min_silence_s is a split point, and
    the voiced spans between such gaps become chunks.

    Spans shorter than min_chunk_s merge into the adjacent span. Any span longer
    than max_chunk_s is hard-split at its quietest interior window, recursively,
    so a continuous loud monologue still lands below whisper's 30s window. Each
    emitted chunk is trimmed to its voiced extent plus ~0.1s padding per side.

    Pure and deterministic: no model, no camera, no state. Returns the whole
    array as one chunk when no usable split is found; returns [] only for
    empty or all-silent input.
    """
    if audio.size == 0:
        return []
    window = max(1, int(sample_rate * 0.03))
    pad = int(sample_rate * 0.1)
    min_silence = int(sample_rate * min_silence_s)
    min_chunk = int(sample_rate * min_chunk_s)
    max_chunk = max(window, int(sample_rate * max_chunk_s))

    # per-window RMS and a voiced/silent mask over the whole signal
    starts = list(range(0, audio.size, window))
    rms = np.array(
        [
            np.sqrt(np.mean(np.square(audio[s : s + window], dtype=np.float64)))
            for s in starts
        ]
    )
    voiced = rms >= silence_thresh
    if not voiced.any():
        return []

    # collapse the window mask into [start, end) voiced spans, splitting only
    # where the silent gap between them is long enough to be a real pause
    spans: list[list[int]] = []
    silent_run = 0
    for i, is_voiced in enumerate(voiced):
        if is_voiced:
            if not spans or silent_run * window >= min_silence:
                spans.append([starts[i], 0])
            silent_run = 0
            spans[-1][1] = min(audio.size, starts[i] + window)
        else:
            silent_run += 1

    # merge sub-min_chunk_s spans into a neighbour so tiny blips never stand alone
    merged: list[list[int]] = []
    for span in spans:
        if span[1] - span[0] < min_chunk and merged:
            merged[-1][1] = span[1]
        else:
            merged.append(span)
    if len(merged) >= 2 and merged[0][1] - merged[0][0] < min_chunk:
        merged[1][0] = merged[0][0]
        merged.pop(0)

    chunks: list[np.ndarray] = []
    for start, end in merged:
        lo = max(0, start - pad)
        hi = min(audio.size, end + pad)
        chunks.extend(_hard_split(audio[lo:hi], window, max_chunk))
    return chunks


def _hard_split(span: np.ndarray, window: int, max_chunk: int) -> list[np.ndarray]:
    """Split an over-long voiced span at its quietest interior window, recursing
    until every piece fits under max_chunk. Keeps a continuous monologue with no
    real pauses below whisper's 30s window."""
    if span.size <= max_chunk:
        return [span]
    # quietest window away from the edges so we cut in a trough, not at a margin
    margin = max(window, span.size // 8)
    starts = list(range(margin, span.size - margin, window)) or [span.size // 2]
    rms = [
        (np.sqrt(np.mean(np.square(span[s : s + window], dtype=np.float64))), s)
        for s in starts
    ]
    cut = min(rms)[1] + window // 2
    return _hard_split(span[:cut], window, max_chunk) + _hard_split(
        span[cut:], window, max_chunk
    )


def spectrum_bands(
    samples: np.ndarray,
    n_bands: int = 24,
    sample_rate: int = SAMPLE_RATE,
) -> list[float]:
    """Log-spaced speech-band magnitude spectrum, each band roughly 0..1.

    Hann-windows ``samples``, takes the real FFT magnitude, and bins it into
    ``n_bands`` log-spaced frequency bands across ~80Hz–8kHz (the speech range).
    Per-band magnitude is normalised by a fixed reference and clipped so quiet
    speech still shows visible motion (scaled in the spirit of ``level()``'s
    ``/0.04`` feel). Empty / all-silence / too-short input returns ``[0.0] *
    n_bands``. Pure and deterministic: no state.
    """
    n_bands = max(1, int(n_bands))
    flat = [0.0] * n_bands
    if samples is None:
        return flat
    samples = np.asarray(samples, dtype=np.float64).ravel()
    n = samples.size
    if n < 16 or not np.any(samples):
        return flat

    window = np.hanning(n)
    windowed = samples * window
    spectrum = np.abs(np.fft.rfft(windowed))
    if spectrum.size < 2:
        return flat
    # normalise FFT magnitude back to a per-sample amplitude scale so the
    # reference below is independent of window length
    spectrum = spectrum / (np.sum(window) + 1e-12) * 2.0
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    lo, hi = 80.0, min(8000.0, sample_rate / 2.0)
    if hi <= lo:
        return flat
    edges = np.logspace(np.log10(lo), np.log10(hi), n_bands + 1)

    ref = 0.04  # same "quiet speech is visible" feel as level()'s /0.04
    out: list[float] = []
    for i in range(n_bands):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if i == n_bands - 1:
            mask = mask | (freqs == edges[i + 1])
        if not mask.any():
            out.append(0.0)
            continue
        mag = float(np.sqrt(np.mean(np.square(spectrum[mask]))))
        out.append(float(min(1.0, mag / ref)))
    return out


class MicRecorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        with self._lock:
            self._chunks = []
        try:
            self._open_stream()
        except Exception:
            # PortAudio snapshots the device list at init; after a hot-swap
            # (headphones on/off) it goes stale and opens fail even though
            # System Settings shows the right mic. Re-scan and retry once so
            # "default input" always means the CURRENT system default.
            print("mic open failed — rescanning audio devices", flush=True)
            sd._terminate()
            sd._initialize()
            self._open_stream()

    def _open_stream(self) -> None:
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
            stream.start()
            self._stream, self._rate = stream, SAMPLE_RATE
        except Exception:
            # some devices (AirPods etc.) refuse 16k; open at native rate
            # and resample in snapshot()
            info = sd.query_devices(kind="input")
            rate = int(info["default_samplerate"])
            stream = sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
            stream.start()
            self._stream, self._rate = stream, rate
            print(f"mic: using {rate}Hz ({info['name']})", flush=True)

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

    def recent(self, seconds: float = 0.05) -> np.ndarray:
        """The last ``seconds`` of mono float32 audio from the tail.

        Same cheap tail-walk as ``level()`` (no full concat); intended to feed
        ``spectrum_bands`` for the live indicator. Returns fewer samples than
        requested if the buffer is shorter, or an empty array if nothing has
        been captured yet. Not resampled — at the native device rate if the mic
        refused 16kHz; pass that rate to ``spectrum_bands`` if you need exact
        frequency bins.
        """
        window = max(1, int(SAMPLE_RATE * max(0.0, seconds)))
        with self._lock:
            tail: list[np.ndarray] = []
            total = 0
            for chunk in reversed(self._chunks):
                tail.append(chunk[:, 0])
                total += chunk.shape[0]
                if total >= window:
                    break
        if not tail:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(tail[::-1])[-window:]

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return self.snapshot()
