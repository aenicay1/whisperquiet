"""On-device transcription via mlx-whisper. The model downloads on first use."""

from __future__ import annotations

import numpy as np

from .audio import SAMPLE_RATE, trim_trailing_silence

MIN_AUDIO_SECONDS = 0.3


def transcribe(
    audio: np.ndarray,
    model_repo: str,
    language: str = "en",
    vocabulary: list[str] | None = None,
) -> str:
    if audio.size < SAMPLE_RATE * MIN_AUDIO_SECONDS:
        return ""
    # whisper hallucinates repeated tokens when it decodes into trailing
    # silence (issue #1), so cut the dead air before it reaches the model
    audio = trim_trailing_silence(audio)
    import inspect

    import mlx_whisper  # deferred: first import loads mlx

    extra = {}
    if vocabulary:
        # a plain glossary string biases the decoder toward those tokens
        extra["initial_prompt"] = " ".join(vocabulary)
    # anti-hallucination decode gates; only pass the ones the installed
    # mlx_whisper accepts (currently all four are available)
    gates = {
        "compression_ratio_threshold": 2.2,
        "logprob_threshold": -1.0,
        "no_speech_threshold": 0.5,
        "hallucination_silence_threshold": 2.0,
    }
    accepted = inspect.signature(mlx_whisper.transcribe).parameters
    extra.update({k: v for k, v in gates.items() if k in accepted})
    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=model_repo,
        language=language,
        # whispered speech has no voicing; condition_on_previous_text tends to
        # compound hallucinations on low-energy audio
        condition_on_previous_text=False,
        **extra,
    )
    return result["text"].strip()


def warm_up(model_repo: str) -> None:
    """Trigger model download/compile at app start instead of first utterance."""
    transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), model_repo)
