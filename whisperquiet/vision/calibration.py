"""Guided gesture calibration: capture per-user blendshape ranges → thresholds.

Walks the user through each gesture (neutral face first, then one gesture at
a time), records the achievable score range, and derives personal on/off
thresholds. Pure Python on plain dicts — testable without a camera; time
comes only from the caller's `t`.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import asdict, fields

from ..control.gestures import GestureConfig

READY_S = 1.5  # "get ready" countdown before each capture
CAPTURE_S = 2.5  # capture window per step

# (step key, HUD instruction, blendshape captured)
STEPS: list[tuple[str, str, str | None]] = [
    ("neutral", "Relax your face and look at the screen", None),
    ("left_wink", "Wink your LEFT eye and hold it", "eyeBlinkLeft"),
    ("right_wink", "Wink your RIGHT eye and hold it", "eyeBlinkRight"),
    ("brow", "Raise your eyebrows and hold", "browInnerUp"),
    ("pucker", "Pucker your lips and hold", "mouthPucker"),
    ("jaw", "Open your mouth wide and hold", "jawOpen"),
]

# fraction of the personal range where a gesture turns on / releases
ON_FRACTION, OFF_FRACTION = 0.55, 0.30
MIN_RANGE = 0.15  # a gesture the user can't move past this is left at defaults


class CalibrationWizard:
    def __init__(
        self,
        on_instruction: Callable[[str, float], None] | None = None,
    ) -> None:
        self._on_instruction = on_instruction or (lambda text, frac: None)
        self._step = 0
        self._phase_start: float | None = None
        self._capturing = False
        self._samples: dict[str, list[dict[str, float]]] = {
            key: [] for key, _, _ in STEPS
        }
        self.done = False

    def process(self, blendshapes: dict[str, float], t: float) -> None:
        """Feed one frame. Sets .done when every step has been captured."""
        if self.done:
            return
        if self._phase_start is None:
            self._phase_start = t
        key, instruction, _ = STEPS[self._step]
        elapsed = t - self._phase_start

        if not self._capturing:
            if elapsed >= READY_S:
                self._capturing, self._phase_start = True, t
            else:
                self._on_instruction(f"Get ready: {instruction}", self._progress(0))
            return

        self._samples[key].append(dict(blendshapes))
        if elapsed < CAPTURE_S:
            self._on_instruction(
                f"{instruction} …", self._progress(elapsed / CAPTURE_S)
            )
            return
        self._step += 1
        self._capturing, self._phase_start = False, None
        if self._step >= len(STEPS):
            self.done = True
            self._on_instruction("Calibration complete", 1.0)

    def _progress(self, within_step: float) -> float:
        return (self._step + within_step) / len(STEPS)

    # -- results -------------------------------------------------------------

    def baseline(self) -> dict[str, float]:
        """Per-key mean over the neutral capture."""
        frames = self._samples["neutral"]
        keys = frames[-1].keys() if frames else []
        return {
            k: statistics.fmean(f.get(k, 0.0) for f in frames) for k in keys
        }

    def build(self) -> tuple[GestureConfig, dict]:
        """Personal GestureConfig + a JSON-safe dict for persistence."""
        base = self.baseline()
        config = GestureConfig()

        def peak(step: str, shape: str) -> float:
            scores = sorted(
                max(0.0, f.get(shape, 0.0) - base.get(shape, 0.0))
                for f in self._samples[step]
            )
            return scores[int(0.9 * (len(scores) - 1))] if scores else 0.0

        mapping = {
            "left_wink": ("eyeBlinkLeft", "wink_on_left", "wink_off_left"),
            "right_wink": ("eyeBlinkRight", "wink_on_right", "wink_off_right"),
            "brow": ("browInnerUp", "brow_on", "brow_off"),
            "pucker": ("mouthPucker", "pucker_on", "pucker_off"),
            "jaw": ("jawOpen", "jaw_on", "jaw_off"),
        }
        for step, (shape, on_field, off_field) in mapping.items():
            rng = peak(step, shape)
            if rng < MIN_RANGE:  # user couldn't perform it; keep defaults
                continue
            on = min(max(ON_FRACTION * rng, 0.2), 0.85)
            setattr(config, on_field, round(on, 3))
            setattr(config, off_field, round(OFF_FRACTION * rng, 3))

        persisted = {
            "baseline": {k: round(v, 4) for k, v in base.items()},
            "config": {
                f.name: getattr(config, f.name) for f in fields(GestureConfig)
            },
        }
        return config, persisted


def config_from_saved(saved: dict) -> tuple[GestureConfig, dict[str, float]]:
    """Rebuild (GestureConfig, baseline) from a persisted wizard result."""
    known = {f.name for f in fields(GestureConfig)}
    cfg_dict = {k: v for k, v in saved.get("config", {}).items() if k in known}
    return GestureConfig(**cfg_dict), dict(saved.get("baseline", {}))
