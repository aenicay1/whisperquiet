"""Verification: webcam + FaceLandmarker must hold ≥30 FPS with <50ms latency.

Runs the live pipeline for ~10s and prints a metrics summary. Exit 0 on pass.
First run triggers the macOS camera permission prompt.
"""

from __future__ import annotations

import statistics
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from whisperquiet.vision.capture import FaceCapture, FaceFrame

DURATION_S = 10.0
frames: list[FaceFrame] = []


def on_frame(frame: FaceFrame) -> None:
    frames.append(frame)


def main() -> int:
    capture = FaceCapture(on_frame)
    capture.start()
    time.sleep(DURATION_S)
    capture.stop()

    if len(frames) < 30:
        print(f"FAIL: only {len(frames)} face frames in {DURATION_S}s — "
              "is the camera permitted and a face visible?")
        return 1

    # skip the first 2s: camera autoexposure + model warm-up
    settled = [f for f in frames if f.timestamp_ms > 2000]
    n = len(settled)
    span = (settled[-1].timestamp_ms - settled[0].timestamp_ms) / 1000
    fps = (n - 1) / span if span > 0 else 0.0
    latencies = sorted(f.latency_ms for f in settled if f.latency_ms >= 0)
    p50 = statistics.median(latencies)
    p95 = latencies[int(0.95 * len(latencies))]
    points = settled[-1].landmarks.shape
    n_blend = len(settled[-1].blendshapes)

    print(f"frames={n} fps={fps:.1f} latency_p50={p50:.1f}ms "
          f"latency_p95={p95:.1f}ms landmarks={points} blendshapes={n_blend}")
    ok = fps >= 30.0 and p95 < 50.0 and points == (468, 3)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
