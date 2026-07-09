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
    n_bands: int = 40,
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

    ref = 0.012  # sensitive: quiet speech should clearly move the bars
    out: list[float] = []
    for i in range(n_bands):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if i == n_bands - 1:
            mask = mask | (freqs == edges[i + 1])
        if not mask.any():
            out.append(0.0)
            continue
        mag = float(np.sqrt(np.mean(np.square(spectrum[mask]))))
        # sqrt curve: perceptual boost so low-energy bands are still visible
        out.append(float(min(1.0, np.sqrt(mag / ref))))
    return out


class MicRecorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        # Only accept callback audio between start() and stop(). Guards against
        # a stream we had to abandon (its close() hung on a wedged device) still
        # firing its callback into a later recording's buffer.
        self._accepting = False
        # bumped on every open; each stream's callback captures its generation
        # and ignores itself once superseded, so an abandoned stream can never
        # write into a newer recording even if its callback keeps firing.
        self._stream_gen = 0

    def start(self, open_timeout: float = 8.0) -> None:
        """Open the mic and begin capturing. Synchronous, run-to-completion.

        MUST be called OFF the main run loop — it runs on the dictation worker
        thread, never the PTT event-tap callback. ``sd.InputStream(...).start()``
        (and the device rescan retried below) can block for a long time, or
        forever, on a device in the AUHAL '-10851' wedged state. On the run loop
        that would freeze the keyboard tap and brick every later push-to-talk
        press (the "hotkey stopped working" bug); on the worker thread it only
        stalls the one in-flight dictation, leaving the hotkey responsive.

        If the open hangs, reset PortAudio from a watchdog timer and raise
        TimeoutError so the app worker can unwind. The caller's worker-alive
        gate guarantees only one open runs at a time, so the process-global
        reset cannot tear down a second concurrent open.
        """
        with self._lock:
            self._chunks = []
        self._accepting = False
        opened = threading.Event()
        timed_out = threading.Event()
        timer = self._start_open_watchdog(open_timeout, opened, timed_out)
        try:
            try:
                self._open_stream(timed_out)
            except Exception:
                if timed_out.is_set():
                    self._recover_after_timed_out_open()
                    raise TimeoutError("mic open timed out")
                raise
            if timed_out.is_set():
                self._recover_after_timed_out_open()
                raise TimeoutError("mic open timed out")
        except Exception:
            if timed_out.is_set():
                raise
            # PortAudio snapshots the device list at init; after a hot-swap
            # (headphones on/off) it goes stale and opens fail even though
            # System Settings shows the right mic. Re-scan and retry once so
            # "default input" always means the CURRENT system default.
            print("mic open failed — rescanning audio devices", flush=True)
            sd._terminate()
            sd._initialize()
            try:
                self._open_stream(timed_out)
            except Exception:
                if timed_out.is_set():
                    self._recover_after_timed_out_open()
                    raise TimeoutError("mic open timed out")
                raise
            if timed_out.is_set():
                self._recover_after_timed_out_open()
                raise TimeoutError("mic open timed out")
        finally:
            opened.set()
            if timer is not None:
                timer.cancel()
        self._accepting = True  # stream is live; accept callback audio

    def _start_open_watchdog(
        self,
        open_timeout: float,
        opened: threading.Event,
        timed_out: threading.Event,
    ) -> threading.Timer | None:
        try:
            timeout = float(open_timeout)
        except (TypeError, ValueError):
            timeout = 0.0
        if timeout <= 0:
            return None

        def abort_open() -> None:
            if opened.is_set():
                return
            timed_out.set()
            print("mic open timed out — resetting PortAudio", flush=True)
            try:
                sd._terminate()
            except Exception:
                pass

        timer = threading.Timer(timeout, abort_open)
        timer.daemon = True
        timer.start()
        return timer

    def _recover_after_timed_out_open(self) -> None:
        self._accepting = False
        stream, self._stream = self._stream, None
        if stream is not None:
            threading.Thread(
                target=self._close_quietly,
                args=(stream,),
                daemon=True,
            ).start()
        try:
            sd._initialize()
        except Exception:
            pass

    def _open_stream(self, timed_out: threading.Event | None = None) -> None:
        self._stream_gen += 1
        callback = self._make_callback(self._stream_gen)

        # Open at the device's NATIVE sample rate, not a forced 16 kHz. Asking a
        # 44.1/48 kHz device (EarPods, AirPods, most external mics) for 16 kHz
        # makes CoreAudio renegotiate the stream format, and that negotiation is
        # the path that trips the AUHAL '-10851' wedge after an input hot-swap.
        # snapshot()/recent() resample to 16 kHz, so whisper still gets 16 kHz
        # mono regardless of the rate we capture at.
        rate, name = SAMPLE_RATE, "?"
        try:
            info = sd.query_devices(kind="input")
            rate = int(info["default_samplerate"]) or SAMPLE_RATE
            name = info["name"]
        except Exception:
            pass  # no device info — fall through to the 16 kHz default

        def _open(at_rate: int) -> None:
            stream = sd.InputStream(
                samplerate=at_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            stream.start()
            self._stream, self._rate = stream, at_rate

        try:
            _open(rate)
            if rate != SAMPLE_RATE:
                print(f"mic: capturing {rate}Hz ({name}) → 16kHz", flush=True)
        except Exception:
            if timed_out is not None and timed_out.is_set():
                raise
            # last resort: the canonical 16 kHz (built-in mics accept it). If the
            # native-rate open failed/blocked first, start()'s rescan precedes the
            # retry that lands here.
            _open(SAMPLE_RATE)
            print("mic: fell back to 16kHz open", flush=True)

    def _make_callback(self, gen: int):
        """Build the audio callback for one stream generation. It appends only
        while accepting AND only if it is still the current stream — so an
        abandoned stream's late callbacks can never pollute a newer recording.
        The accepting/gen check is inside the lock with the append, so it is
        atomic against stop()/start() flipping the flags (no torn check-then-act,
        which matters under free-threaded Python)."""
        def _callback(indata, frames, time_info, status) -> None:
            with self._lock:
                if not self._accepting or gen != self._stream_gen:
                    return
                self._chunks.append(indata.copy())
        return _callback

    def snapshot(self) -> np.ndarray:
        """All audio captured so far, mono float32 at 16kHz. Safe while recording."""
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks)[:, 0]
            # read _rate under the same lock so a concurrent start() can't swap
            # in a different rate between the concat and the resample
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
        """The last ``seconds`` of mono float32 audio, resampled to 16kHz.

        Cheap tail-walk (no full concat); feeds ``spectrum_bands`` for the live
        indicator. Resampled to 16kHz so the band/frequency mapping is correct
        no matter the device's native capture rate (now that we capture at the
        device rate by default). Returns fewer samples than requested if the
        buffer is shorter, or an empty array if nothing has been captured yet.
        """
        with self._lock:
            rate = getattr(self, "_rate", SAMPLE_RATE)
            window = max(1, int(rate * max(0.0, seconds)))
            tail: list[np.ndarray] = []
            total = 0
            for chunk in reversed(self._chunks):
                tail.append(chunk[:, 0])
                total += chunk.shape[0]
                if total >= window:
                    break
        if not tail:
            return np.zeros(0, dtype=np.float32)
        samples = np.concatenate(tail[::-1])[-window:]
        if rate != SAMPLE_RATE and samples.size:
            n_out = max(1, int(samples.size * SAMPLE_RATE / rate))
            samples = np.interp(
                np.linspace(0, samples.size - 1, n_out),
                np.arange(samples.size),
                samples,
            ).astype(np.float32)
        return samples

    def stop(self, close_timeout: float = 2.0) -> np.ndarray:
        """Stop recording and return the captured audio.

        PortAudio stop()/close() can block forever when the input device is in
        a bad state (the AUHAL '-10851' wedge after a hot-swap). That used to
        hang the dictation worker indefinitely, which silently bricked every
        later push-to-talk. So close the stream on a watchdog thread and, if it
        does not return within close_timeout, ABANDON it (leak the wedged stream
        object) and return the audio we already captured. _accepting is cleared
        first so the abandoned stream's callback can never pollute a later take.
        """
        self._accepting = False
        stream, self._stream = self._stream, None
        if stream is not None:
            done = threading.Event()

            def _close() -> None:
                try:
                    self._close_quietly(stream)
                finally:
                    done.set()

            threading.Thread(target=_close, daemon=True).start()
            if not done.wait(close_timeout):
                print("mic stop timed out — abandoning wedged stream", flush=True)
        return self.snapshot()

    @staticmethod
    def _close_quietly(stream) -> None:
        """Best-effort stop+close that never raises (used by stop()'s watchdog).
        May itself block on a wedged device, so the caller runs it on a thread it
        can abandon."""
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
