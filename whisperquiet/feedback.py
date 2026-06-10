"""Dogfood feedback events, appended as JSON lines next to stats.jsonl.

Kinds: "flag" (user marked something wrong; detail carries the recent
stats ring) and "dictation_edited" (user typed within seconds of a
commit; detail: {"words": int, "keys": int}).

A feedback failure must never break dictation, so all IO errors are swallowed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import CONFIG_DIR

FEEDBACK_PATH = CONFIG_DIR / "feedback.jsonl"


class FeedbackLog:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else FEEDBACK_PATH

    def log(self, kind: str, detail: dict | None = None) -> None:
        """Append one event, e.g. "flag", "dictation_edited". Never raises."""
        line = json.dumps({"ts": int(time.time()), "kind": kind, "detail": detail or {}})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            pass
