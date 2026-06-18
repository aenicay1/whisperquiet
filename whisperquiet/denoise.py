"""Optional noise-suppression front-end, for measurement before adoption.

A denoiser that cleans the mic signal before whisper sees it is the cheapest
single WER lever in noisy rooms (cafés, fans, AC) per the research — but
aggressive suppression can strip the phonemes the ASR needs and HURT accuracy,
especially on low-energy whispered speech. So this is NOT wired into the live
dictation path. It exists to be A/B'd offline by scripts/bench_denoise.py: run
the recorded takes with and without it and compare WER per environment. It gets
wired into the app, behind a config flag, only after a measured per-environment
win (see docs/BACKLOG.md).

The actual algorithm is an optional dependency (spectral-gating ``noisereduce``,
which is numpy/scipy only — no torch), imported lazily so the package has no new
hard dependency. ``available()`` reports whether it can run; ``reduce_noise``
raises ImportError with the install hint if not.
"""

from __future__ import annotations

import numpy as np

from .audio import SAMPLE_RATE

_INSTALL_HINT = "pip install -e '.[denoise]'"


def available() -> bool:
    """True if the optional denoiser dependency is importable."""
    try:
        import noisereduce  # noqa: F401  optional dependency
    except ImportError:
        return False
    return True


def reduce_noise(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    stationary: bool = True,
) -> np.ndarray:
    """Spectral-gating noise reduction, returning mono float32 at the input rate.

    Mirrors the live pipeline's dtype contract (float32) so a bench A/B isolates
    the denoiser, not a format change. ``stationary=True`` models a steady noise
    floor (fan/AC/hum), which is the case the denoiser is meant to help; pass
    False for non-stationary suppression. Raises ImportError (with the install
    hint) when the optional dependency is missing, so the bench can report the
    leg as unavailable rather than crash.
    """
    try:
        import noisereduce as nr  # optional dependency
    except ImportError as exc:  # pragma: no cover - exercised via available()
        raise ImportError(
            f"denoise backend not installed — {_INSTALL_HINT}"
        ) from exc
    samples = np.asarray(audio, dtype=np.float32).ravel()
    if samples.size == 0:
        return samples
    out = nr.reduce_noise(y=samples, sr=sample_rate, stationary=stationary)
    return np.asarray(out, dtype=np.float32)
