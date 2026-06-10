"""Webcam → MediaPipe FaceLandmarker live-stream pipeline.

Emits one FaceFrame per camera frame: 468 3D landmarks + 52 blendshape
scores. Runs detection in MediaPipe's async live-stream mode so the camera
read loop never blocks on inference.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

DEFAULT_MODEL = Path(__file__).resolve().parents[2] / "models" / "face_landmarker.task"


@dataclass
class FaceFrame:
    landmarks: np.ndarray  # (468, 3) normalized x, y, z
    blendshapes: dict[str, float]  # 52 ARKit-style scores
    timestamp_ms: int
    fps: float  # EMA of delivery rate
    latency_ms: float  # camera read → result callback


class FaceCapture:
    def __init__(
        self,
        on_frame: Callable[[FaceFrame], None],
        model_path: Path = DEFAULT_MODEL,
        camera_index: int = 0,
    ) -> None:
        self._on_frame = on_frame
        self._model_path = model_path
        self._camera_index = camera_index
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._landmarker = None
        self._fps = 0.0
        self._last_result_t: float | None = None
        self._sent_at: dict[int, float] = {}

    def start(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python import vision as mp_vision

        options = mp_vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(self._model_path)),
            running_mode=mp_vision.RunningMode.LIVE_STREAM,
            output_face_blendshapes=True,
            num_faces=1,
            result_callback=self._on_result,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        self._running.set()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def _read_loop(self) -> None:
        import mediapipe as mp

        cap = cv2.VideoCapture(self._camera_index)
        try:
            t0 = time.monotonic()
            while self._running.is_set():
                ok, frame_bgr = cap.read()
                if not ok:
                    time.sleep(0.01)
                    continue
                ts_ms = int((time.monotonic() - t0) * 1000)
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                self._sent_at[ts_ms] = time.monotonic()
                self._landmarker.detect_async(image, ts_ms)
        finally:
            cap.release()

    def _on_result(self, result, output_image, timestamp_ms: int) -> None:
        sent = self._sent_at.pop(timestamp_ms, None)
        # drop stale send-time entries (frames mediapipe skipped under load)
        for key in [k for k in self._sent_at if k < timestamp_ms]:
            self._sent_at.pop(key, None)
        if not result.face_landmarks:
            return
        now = time.monotonic()
        if self._last_result_t is not None:
            inst = 1.0 / max(now - self._last_result_t, 1e-6)
            self._fps = inst if self._fps == 0 else 0.9 * self._fps + 0.1 * inst
        self._last_result_t = now

        face = result.face_landmarks[0]
        landmarks = np.array([[p.x, p.y, p.z] for p in face], dtype=np.float32)
        blendshapes = {}
        if result.face_blendshapes:
            blendshapes = {
                b.category_name: b.score for b in result.face_blendshapes[0]
            }
        self._on_frame(
            FaceFrame(
                landmarks=landmarks,
                blendshapes=blendshapes,
                timestamp_ms=timestamp_ms,
                fps=self._fps,
                latency_ms=(now - sent) * 1000 if sent else -1.0,
            )
        )
