#!/usr/bin/env python3
"""Open/close the real default mic through the supervised audio child.

This is the fast production feedback loop for the failure where CoreAudio
wedges during stream open or close. It exercises the exact process boundary the
app uses without loading Whisper or launching the menu-bar UI.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisperquiet.audio_process import ProcessMicRecorder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0.5)
    parser.add_argument("--open-timeout", type=float, default=8.0)
    parser.add_argument("--close-timeout", type=float, default=2.0)
    args = parser.parse_args()

    recorder = ProcessMicRecorder()
    started = time.perf_counter()
    recorder.start(open_timeout=args.open_timeout)
    open_ms = int((time.perf_counter() - started) * 1000)
    time.sleep(max(0.05, args.seconds))
    audio = recorder.stop(close_timeout=args.close_timeout)
    rms = float((audio.astype("float64") ** 2).mean() ** 0.5) if audio.size else 0.0
    print(
        "audio supervisor PASS:"
        f" open={open_ms}ms samples={audio.size}"
        f" seconds={audio.size / 16000:.2f} rms={rms:.5f}",
        flush=True,
    )
    return 0 if audio.size else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
