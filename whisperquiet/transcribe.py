"""On-device transcription via mlx-whisper. The model downloads on first use."""

from __future__ import annotations

import numpy as np

from .audio import SAMPLE_RATE

MIN_AUDIO_SECONDS = 0.3


def transcribe(audio: np.ndarray, model_repo: str, language: str = "en") -> str:
    if audio.size < SAMPLE_RATE * MIN_AUDIO_SECONDS:
        return ""
    import mlx_whisper  # deferred: first import loads mlx

    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model_repo,
        language=language,
        # whispered speech has no voicing; condition_on_previous_text tends to
        # compound hallucinations on low-energy audio
        condition_on_previous_text=False,
    )
    return result["text"].strip()


def warm_up(model_repo: str) -> None:
    """Trigger model download/compile at app start instead of first utterance."""
    transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), model_repo)
