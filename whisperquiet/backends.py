"""Dictation backend selection: whisper (default) vs parakeet (opt-in).

Call sites stay backend-agnostic — they ask for a backend + the repo to feed it
and then use the shared transcribe / transcribe_long / warm_up surface:

    backend, repo = backends.get_backend(config)
    text = backend.transcribe(audio, repo, config.language, vocabulary=...)

The default is "whisper" (mlx-whisper), so behaviour is unchanged unless the
user sets config.dictation_backend to "parakeet" AND installs the optional
[parakeet] extra. The parakeet module is imported lazily inside the branch; that
import does not pull in parakeet_mlx itself (it is deferred one level deeper, in
parakeet.py), so an unset/whisper flag never touches the optional dependency.
"""

from __future__ import annotations

from . import transcribe as _whisper

WHISPER = "whisper"
PARAKEET = "parakeet"

DEFAULT_PARAKEET_REPO = "mlx-community/parakeet-tdt-0.6b-v3"


def get_backend(config):
    """Return ``(backend_module, model_repo)`` for ``config.dictation_backend``.

    Falls back to the whisper backend for any unknown value, so a typo in the
    config can never leave the app with no transcriber.
    """
    if getattr(config, "dictation_backend", WHISPER) == PARAKEET:
        from . import parakeet as _parakeet  # optional backend, loaded on demand

        return _parakeet, getattr(config, "parakeet_repo", DEFAULT_PARAKEET_REPO)
    return _whisper, config.model_repo
