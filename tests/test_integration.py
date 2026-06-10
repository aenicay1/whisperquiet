"""Integration harness: synthetic vision frames → gesture engine → OS stub.

Runs the real threading topology (producer thread feeding frames, consumer
dispatching events) without a camera, checking for race conditions, missed
events, and per-frame latency over budget.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from whisperquiet.control.gestures import GestureEngine, GestureEvent

FPS = 30
DT = 1.0 / FPS
LATENCY_BUDGET_MS = 50.0

NEUTRAL = {
    "eyeBlinkLeft": 0.05,
    "eyeBlinkRight": 0.05,
    "browInnerUp": 0.1,
    "mouthPucker": 0.1,
    "jawOpen": 0.05,
}


def synthetic_stream(script: list[tuple[float, dict[str, float]]]):
    """Expand [(duration_s, overrides)] into per-frame (t, blendshapes)."""
    frames, t = [], 0.0
    for duration, overrides in script:
        for _ in range(int(duration * FPS)):
            frames.append((t, {**NEUTRAL, **overrides}))
            t += DT
    return frames


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[GestureEvent] = []
        self.lock = threading.Lock()

    def __call__(self, event: GestureEvent) -> None:
        with self.lock:
            self.events.append(event)


def run_pipeline(frames, recorder) -> float:
    """Feed frames through the engine on a worker thread, like FaceCapture
    does, and return the worst per-frame processing time in ms."""
    engine = GestureEngine(recorder)
    engine.set_baseline(NEUTRAL)
    worst_ms = 0.0

    def producer() -> None:
        nonlocal worst_ms
        for t, shapes in frames:
            start = time.perf_counter()
            engine.process(shapes, t)
            worst_ms = max(worst_ms, (time.perf_counter() - start) * 1000)

    thread = threading.Thread(target=producer)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "pipeline thread hung"
    return worst_ms


def test_wink_to_click_end_to_end():
    recorder = EventRecorder()
    frames = synthetic_stream([
        (1.0, {}),
        (0.3, {"eyeBlinkLeft": 0.9}),  # deliberate left wink
        (1.0, {}),
        (0.3, {"eyeBlinkRight": 0.9}),  # deliberate right wink
        (1.0, {}),
    ])
    run_pipeline(frames, recorder)
    assert recorder.events == [GestureEvent.LEFT_CLICK, GestureEvent.RIGHT_CLICK]


def test_natural_blinks_fire_nothing():
    recorder = EventRecorder()
    blink = [(0.15, {"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9}), (0.5, {})]
    frames = synthetic_stream([(1.0, {})] + blink * 10)
    run_pipeline(frames, recorder)
    assert recorder.events == []


def test_scroll_repeats_while_held():
    recorder = EventRecorder()
    frames = synthetic_stream([
        (0.5, {}),
        (1.0, {"browInnerUp": 0.8}),
        (0.5, {}),
    ])
    run_pipeline(frames, recorder)
    scrolls = [e for e in recorder.events if e is GestureEvent.SCROLL_UP]
    assert 4 <= len(scrolls) <= 8  # ~1s held, first at 0.15s then every 0.15s
    assert set(recorder.events) == {GestureEvent.SCROLL_UP}


def test_per_frame_latency_within_budget():
    recorder = EventRecorder()
    frames = synthetic_stream([(10.0, {"browInnerUp": 0.8})])
    worst_ms = run_pipeline(frames, recorder)
    assert worst_ms < LATENCY_BUDGET_MS, f"worst frame {worst_ms:.2f}ms"


def test_concurrent_baseline_update_does_not_crash():
    """Calibration can rebase while frames are flowing — must not race."""
    recorder = EventRecorder()
    engine = GestureEngine(recorder)
    stop = threading.Event()
    errors: list[Exception] = []

    def feed() -> None:
        t = 0.0
        try:
            while not stop.is_set():
                engine.process(dict(NEUTRAL), t)
                t += DT
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def rebase() -> None:
        try:
            for _ in range(200):
                engine.set_baseline(dict(NEUTRAL))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    feeder = threading.Thread(target=feed)
    feeder.start()
    rebaser = threading.Thread(target=rebase)
    rebaser.start()
    rebaser.join(timeout=5)
    stop.set()
    feeder.join(timeout=5)
    assert errors == []
    assert recorder.events == []


@pytest.mark.skipif(
    __import__("os").environ.get("WQ_CAMERA_TESTS") != "1",
    reason="needs camera + GUI session; set WQ_CAMERA_TESTS=1",
)
def test_live_camera_fps():
    from whisperquiet.vision.capture import FaceCapture

    frames = []
    capture = FaceCapture(frames.append)
    capture.start()
    time.sleep(5)
    capture.stop()
    settled = [f for f in frames if f.timestamp_ms > 2000]
    assert len(settled) >= 85  # ≥ ~28fps over the settled 3s
