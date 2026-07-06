"""Optional NVIDIA Parakeet TDT backend via parakeet-mlx.

OFF by default — reached only when config.dictation_backend == "parakeet"
(see backends.get_backend). parakeet_mlx is imported lazily inside the functions
that talk to the model, so the dependency stays optional: install it with the
``[parakeet]`` extra (``pip install -e '.[parakeet]'``). With the flag on its
default "whisper", nothing here ever imports parakeet_mlx.

This mirrors the public surface of transcribe.py — transcribe / transcribe_long
/ warm_up with the same signatures — so the backend selector can swap it in for
mlx-whisper at every call site without the app caring which model runs.

Parakeet emits punctuation, capitalization, and inverse text normalization
natively; it does NOT change cleanup.py / incremental.py here. Its reason to
exist today is the data-driven turbo-vs-parakeet comparison in
scripts/bench_backends.py — this module is the real backend that benchmark
exercises, not a throwaway. The swap of the shipping default is a separate,
data-gated decision and is not made in this file.

Caveats baked in, matching 2026 reality of the MLX port:
  * No hotword / word-boosting is exposed by parakeet-mlx (NeMo has shallow-
    fusion biasing; the MLX port does not surface it), so ``vocabulary`` is
    accepted for signature parity with the whisper backend and ignored.
  * v3 covers English + 25 EU languages (vs whisper's 99); ``language`` is
    accepted for parity but the multilingual model does its own LID, so it is
    not forwarded.
"""

from __future__ import annotations

import gc
import tempfile
import wave
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE, peak_normalize, trim_trailing_silence
from .transcribe import MIN_AUDIO_SECONDS

# Audio longer than this is handed to parakeet-mlx's own chunked decode
# (chunk_duration/overlap_duration) instead of one shot — the analogue of the
# whisper backend's transcribe_long silence-splitting, but native to the model.
_LONG_AUDIO_SECONDS = 60.0
_CHUNK_DURATION = 120.0
_OVERLAP_DURATION = 15.0

# repo -> loaded model. parakeet-mlx weights are large; load once and keep the
# instance resident (the app keeps the whisper model warm the same way).
_MODELS: dict = {}


def _load(model_repo: str):
    """Load (and cache) the parakeet-mlx model for ``model_repo``.

    Deferred import: parakeet_mlx is only touched here, so importing this module
    never pulls in the optional dependency.
    """
    if model_repo not in _MODELS:
        from parakeet_mlx import from_pretrained  # optional dependency

        _MODELS[model_repo] = from_pretrained(model_repo)
    return _MODELS[model_repo]


def _write_wav(audio: np.ndarray, path: str) -> None:
    """Write mono float32 [-1, 1] audio as a 16 kHz int16 WAV at ``path``.

    parakeet-mlx loads audio from a file path; we hand it a 16 kHz WAV (the
    model's native rate) so its loader never has to resample.
    """
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def transcribe(
    audio: np.ndarray,
    model_repo: str,
    language: str = "en",
    vocabulary: list[str] | None = None,
) -> str:
    """Transcribe ``audio`` with parakeet-mlx, mirroring transcribe.transcribe.

    Applies the SAME pre-processing the whisper path applies — trailing-silence
    trim then peak-normalize — so a bench_backends A/B isolates the model, not
    the front-end. Whispered speech is low-amplitude regardless of which model
    decodes it, so the normalize rationale carries over.
    """
    if audio.size < SAMPLE_RATE * MIN_AUDIO_SECONDS:
        return ""
    audio = peak_normalize(trim_trailing_silence(audio))
    model = _load(model_repo)

    kwargs = {}
    if audio.size > SAMPLE_RATE * _LONG_AUDIO_SECONDS:
        kwargs = {"chunk_duration": _CHUNK_DURATION, "overlap_duration": _OVERLAP_DURATION}

    # parakeet-mlx reads from a path; write the preprocessed buffer to a temp
    # WAV, decode, and clean up. (language/vocabulary intentionally unused — see
    # module docstring.)
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = str(Path(tmp) / "utt.wav")
        _write_wav(audio, wav_path)
        result = model.transcribe(wav_path, **kwargs)
    return (getattr(result, "text", "") or "").strip()


def transcribe_long(
    audio: np.ndarray,
    model_repo: str,
    language: str = "en",
    vocabulary: list[str] | None = None,
) -> str:
    """Long-form transcription.

    The whisper backend splits on silence because one-shot decoding of a long
    buffer drops middle sentences; parakeet-mlx handles long audio itself via
    chunk_duration/overlap_duration, which transcribe() already enables past
    ``_LONG_AUDIO_SECONDS``. So this is just transcribe().
    """
    return transcribe(audio, model_repo, language=language, vocabulary=vocabulary)


def warm_up(model_repo: str) -> None:
    """Trigger model download/load at app start instead of first utterance.

    Best-effort and never raises: if parakeet_mlx is missing or the load fails,
    the error surfaces on the first real transcribe() call (where it can be
    handled in context), not from a background warm-up thread.
    """
    try:
        transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), model_repo)
    except Exception:
        pass


def unload_model(model_repo: str | None = None) -> bool:
    """Drop cached parakeet model instances for idle memory relief."""
    if model_repo is None:
        unloaded = bool(_MODELS)
        _MODELS.clear()
    else:
        unloaded = model_repo in _MODELS
        _MODELS.pop(model_repo, None)
    if unloaded:
        gc.collect()
    return unloaded
