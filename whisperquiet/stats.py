"""Session metrics for the dogfooding week, appended as JSON lines.

DESIGN.md success bar: <1 false click/hour, <5 trackpad touches/hour.
A stats failure must never break dictation, so all IO errors are swallowed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .config import CONFIG_DIR

STATS_PATH = CONFIG_DIR / "stats.jsonl"


class SessionStats:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else STATS_PATH

    def record(self, kind: str, n: int = 1) -> None:
        """Append one event, e.g. "left_click", "words", "dictation", "pause".

        Open/append/close per call: cheap, crash-safe, and thread-safe
        enough via line atomicity. Never raises to the caller.
        """
        line = json.dumps({"ts": int(time.time()), "kind": kind, "n": n})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def summary(self, day: str | None = None) -> dict[str, int]:
        """Totals per kind for the given YYYY-MM-DD (local time), today by default."""
        if day is None:
            day = datetime.now().strftime("%Y-%m-%d")
        totals: dict[str, int] = {}
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return totals
        for line in lines:
            try:
                event = json.loads(line)
                ts, kind, n = event["ts"], event["kind"], event["n"]
            except (ValueError, KeyError):
                continue  # torn or foreign line; skip
            if datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == day:
                totals[kind] = totals.get(kind, 0) + n
        return totals
