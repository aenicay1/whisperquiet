"""Conservative speech-presence gate for the committed dictation.

The goal is narrow and safety-first: never let the app COMMIT text that was
decoded from audio containing no speech — a silent push-to-talk press, or a
steady tonal noise (AC/fan whine, 60 Hz electrical hum) that whisper happily
hallucinates words from. It is NOT a general VAD and deliberately errs toward
saying "speech": whispered speech is low-amplitude and broadband, exactly the
signal we must never gate out, so the only things this rejects are

  * silence / near-silence (whole-clip RMS below an absolute floor), and
  * a signal dominated by a single narrow frequency (a pure-ish tone: low
    spectral flatness AND most energy in one FFT bin).

White/broadband noise (breath, room noise) is intentionally PASSED through to
the decoder's own anti-hallucination gates — rejecting it here would risk
swallowing real whispered speech, which is the worse failure for this app.

Pure and deterministic: numpy only, no model, no state, no I/O. A Silero ONNX
detector is the planned upgrade (see docs/BACKLOG.md) before the gate is ever
turned on by default; this built-in is the safe interim and the seam the bench
exercises.
"""

from __future__ import annotations

import numpy as np

from .audio import SAMPLE_RATE


def is_speech(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    energy_floor: float = 3e-5,
    tone_flatness: float = 0.06,
    tone_peak_fraction: float = 0.55,
) -> bool:
    """Return True if ``audio`` plausibly contains speech.

    Conservative: returns False only for clear silence or a clear single tone;
    anything else (including low-energy whispered speech and broadband noise)
    returns True.

    energy_floor: whole-clip RMS below this is treated as silence. Set well
        below the measured RMS of real whispered speech (~5e-5..1.5e-4) so the
        gate catches only true silence/near-silence and never the quiet whisper
        the app exists to capture — gating out real whisper is the worst
        possible failure here, so the floor errs low.
    tone_flatness: spectral flatness (geometric/arithmetic mean of the power
        spectrum) below this is "tonal". Speech sits well above it; a pure sine
        is ~0.
    tone_peak_fraction: fraction of total spectral power in the single loudest
        bin above which the signal is considered dominated by one frequency.
        Both the flatness and the peak-fraction tests must fire to reject as a
        tone, so a formant-rich speech spectrum is never mistaken for one.
    """
    if audio is None:
        return False
    samples = np.asarray(audio, dtype=np.float64).ravel()
    if samples.size < 16:
        return False

    rms = float(np.sqrt(np.mean(np.square(samples))))
    if rms < energy_floor:
        return False  # silence / near-silence

    # power spectrum over the speech band; a steady tone concentrates in one bin
    window = np.hanning(samples.size)
    spectrum = np.abs(np.fft.rfft(samples * window)) ** 2
    if spectrum.size < 4:
        return True  # too short to characterise; don't gate it out
    # ignore the DC / sub-speech bins so a slow drift or DC offset is not read
    # as a dominant "tone". NB: this 60 Hz cutoff means 50 Hz mains hum is not
    # caught as a tone, and FFT leakage can let a ~60 Hz tone slip through; the
    # planned Silero ONNX detector (docs/BACKLOG.md) supersedes these heuristics
    # before the gate is ever enabled by default.
    freqs = np.fft.rfftfreq(samples.size, d=1.0 / sample_rate)
    spectrum = spectrum[freqs >= 60.0]
    total = float(np.sum(spectrum))
    if spectrum.size < 4 or total <= 0.0:
        return True

    peak_fraction = float(np.max(spectrum)) / total
    # spectral flatness: geometric mean / arithmetic mean of the power spectrum
    geo = float(np.exp(np.mean(np.log(spectrum + 1e-20))))
    arith = total / spectrum.size
    flatness = geo / arith if arith > 0 else 1.0

    if flatness < tone_flatness and peak_fraction > tone_peak_fraction:
        return False  # dominated by a single frequency: a tone, not speech
    return True
