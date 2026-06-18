"""User configuration, persisted as JSON in ~/Library/Application Support/whisperquiet."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "whisperquiet"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class Config:
    # key names from hotkey.KEYCODES
    ptt_key: str = "alt_r"
    # tap this key right after a bad gesture/dictation to flag it for review
    flag_key: str = "shift_r"
    # HuggingFace repo for the MLX whisper model
    model_repo: str = "mlx-community/whisper-large-v3-turbo"
    # Which dictation backend to use: "whisper" (mlx-whisper, the shipping
    # default) or "parakeet" (parakeet-mlx, opt-in — requires the optional
    # [parakeet] extra). Selected via backends.get_backend; the turbo-vs-parakeet
    # choice is meant to be made from scripts/bench_backends.py data, not flipped
    # blindly. Stays "whisper" until that comparison says otherwise.
    dictation_backend: str = "whisper"
    # HuggingFace repo for the parakeet model (used only when dictation_backend
    # == "parakeet"). v3 = English + 25 EU languages, native punctuation/ITN.
    parakeet_repo: str = "mlx-community/parakeet-tdt-0.6b-v3"
    language: str = "en"
    # Seconds between streaming re-transcriptions while PTT is held
    stream_interval: float = 0.7
    # "keystrokes" injects unicode key events; "paste" uses clipboard + Cmd-V
    inject_mode: str = "keystrokes"
    # rules-based filler/repeat/correction cleanup on dictation output
    cleanup_enabled: bool = True
    # optional on-device LLM rescoring stage (fixes implausible recognition
    # errors); OFF by default — opt-in, requires the optional mlx_lm dependency
    rescore_enabled: bool = False
    # hard speech-presence gate on the committed dictation (whisperquiet/vad.py):
    # drops the result when the audio is silence or steady tonal noise so it is
    # never committed as a hallucination. OFF by default — the built-in detector
    # is conservative on purpose (it must never swallow low-energy whispered
    # speech); turn it on per-environment only after a measured win. A Silero
    # ONNX detector is the planned upgrade before this becomes a default.
    vad_gate_enabled: bool = False
    # keep raw->cleaned transcript pairs on-device (personalization corpus)
    keep_transcripts: bool = True
    # save each dictation's audio as WAV on-device (WER benchmarking + LoRA)
    keep_audio: bool = True
    # Gesture/cursor settings, calibration, tunables, experimental flags
    gestures: dict = field(default_factory=dict)
    # Names/jargon the user dictates often; biases the whisper decoder
    vocabulary: list[str] = field(default_factory=list)


def load() -> Config:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        known = {f for f in Config.__dataclass_fields__}
        return Config(**{k: v for k, v in data.items() if k in known})
    return Config()


def save(config: Config) -> None:
    """Persist config atomically.

    The settings bridge (settings_server) and the main app thread can both call
    save(); a plain write_text() can interleave or be torn by a crash and leave
    a half-written config.json that load() then fails to parse, losing every
    user tunable. Write to a temp file in the same directory, fsync it, then
    os.replace onto the target — os.replace is atomic on the same filesystem, so
    a reader (or a crash) only ever sees the old file or the complete new one.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps(asdict(config), indent=2)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            fd = None  # fdopen now owns it; the with-block closes it
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        # never leak the fd (if fdopen itself failed) or leave a stray temp file
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
