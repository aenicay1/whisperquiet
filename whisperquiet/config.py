"""User configuration, persisted as JSON in ~/Library/Application Support/whisperquiet."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / "Library" / "Application Support" / "whisperquiet"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class Config:
    # pynput key name for push-to-talk (see pynput.keyboard.Key members)
    ptt_key: str = "alt_r"
    # HuggingFace repo for the MLX whisper model
    model_repo: str = "mlx-community/whisper-large-v3-turbo"
    language: str = "en"
    # Seconds between streaming re-transcriptions while PTT is held
    stream_interval: float = 0.7
    # "keystrokes" injects unicode key events; "paste" uses clipboard + Cmd-V
    inject_mode: str = "keystrokes"
    # Gesture/cursor settings land here in week 3-4 (see whisperquiet/control/)
    gestures: dict = field(default_factory=dict)


def load() -> Config:
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())
        known = {f for f in Config.__dataclass_fields__}
        return Config(**{k: v for k, v in data.items() if k in known})
    return Config()


def save(config: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2))
