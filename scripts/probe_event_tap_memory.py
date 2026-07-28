#!/usr/bin/env python3
"""Assert that the running app does not retain one object graph per input event.

This is a macOS release probe, not a CI unit test. It targets an already-running
WhisperQuiet process whose log has confirmed ``PTT tap installed``, snapshots
its live heap, posts harmless tagged F15 events, then snapshots again. The
tagged events are observed by WhisperQuiet's listen-only tap but ignored by its
product behavior.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import Quartz

# Executing this file directly puts ``scripts/`` on sys.path, not the repository
# root. Keep the release probe self-contained instead of relying on an editable
# install in whichever virtual environment happens to run it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whisperquiet.inject import SYNTHETIC_TAG

_LEAK_TYPES = ("CGEvent", "CGSEventAppendix", "HIDEvent")
_COUNT_RE = re.compile(r"^\s*(\d+)\s+")
_F15_KEYCODE = 113


def _heap_counts(pid: int) -> dict[str, int]:
    result = subprocess.run(
        ["/usr/bin/heap", "-q", "-s", "-H", str(pid)],
        check=True,
        capture_output=True,
        text=True,
    )
    counts = {name: 0 for name in _LEAK_TYPES}
    for line in result.stdout.splitlines():
        for name in _LEAK_TYPES:
            if re.search(rf"\b{re.escape(name)}\b", line):
                match = _COUNT_RE.match(line)
                if match:
                    counts[name] = int(match.group(1))
                break
    return counts


def _post_tagged_events(pairs: int, delay_seconds: float) -> None:
    for _ in range(pairs):
        for key_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(
                None, _F15_KEYCODE, key_down
            )
            Quartz.CGEventSetIntegerValueField(
                event,
                Quartz.kCGEventSourceUserData,
                SYNTHETIC_TAG,
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            if delay_seconds:
                time.sleep(delay_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--pairs", type=int, default=2_000)
    parser.add_argument("--delay-ms", type=float, default=0.5)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    parser.add_argument("--max-growth", type=int, default=5)
    args = parser.parse_args()

    os.kill(args.pid, 0)
    before = _heap_counts(args.pid)
    _post_tagged_events(args.pairs, max(0.0, args.delay_ms) / 1_000.0)
    time.sleep(max(0.0, args.settle_seconds))
    os.kill(args.pid, 0)
    after = _heap_counts(args.pid)

    growth = {name: after[name] - before[name] for name in _LEAK_TYPES}
    print(
        "event tap heap:"
        f" before={before}"
        f" after={after}"
        f" growth={growth}"
    )
    failures = {
        name: count for name, count in growth.items() if count > args.max_growth
    }
    if failures:
        print(
            "event tap memory FAIL:"
            f" retained event objects exceeded {args.max_growth}: {failures}"
        )
        return 1
    print(
        "event tap memory PASS:"
        f" {args.pairs * 2} posted events without retained object growth"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
