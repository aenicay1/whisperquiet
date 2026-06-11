"""Head-pose → cursor mapping (DESIGN.md decision #9).

Relative displacement, no calibration step: feed the normalized [0,1]
position of a tracked face point (nose tip) each frame and get back
screen-pixel deltas. Pure Python, camera-free — time comes only from the
caller's `t` (monotonic seconds), never the wall clock, so tests are
deterministic.

Pipeline per frame:
- One-Euro filter each axis on the raw position (jitter vs lag, decision #9).
- delta = filtered - previous filtered.
- Deadzone on the delta magnitude kills residual jitter at rest.
- Scale by gain_px. dx is NEGATED: the camera frame is mirrored, so the
  nose moving right in the image means the head moved right — and the
  cursor should too. dy is positive when nose y grows, matching CG screen
  coords (y grows downward).
- Precision mode (squint-hold or auto-slowdown) scales gain down for
  pixel work.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .one_euro import OneEuroFilter


@dataclass
class CursorConfig:
    # Screen pixels per unit of normalized nose travel.
    gain_px: float = 3500.0
    # Normalized; applied to the per-frame filtered delta magnitude.
    deadzone: float = 0.0015
    # Gain multiplier while precision mode is on.
    precision_scale: float = 0.3
    # One-Euro tunables (decision #9): min_cutoff trades jitter for lag at
    # rest, beta trades lag for jitter in motion.
    min_cutoff: float = 1.0
    beta: float = 0.05


class HeadCursor:
    def __init__(self, config: CursorConfig | None = None) -> None:
        self.config = config or CursorConfig()
        self._fx = OneEuroFilter(self.config.min_cutoff, self.config.beta)
        self._fy = OneEuroFilter(self.config.min_cutoff, self.config.beta)
        self._prev: tuple[float, float] | None = None
        self._precision = False

    def process(self, x: float, y: float, t: float) -> tuple[float, float] | None:
        """Feed the normalized [0,1] position of a tracked face point (nose tip)
        each frame; returns (dx, dy) screen-pixel deltas, or None (deadzone/first
        frame)."""
        fx = self._fx(x, t)
        fy = self._fy(y, t)
        prev, self._prev = self._prev, (fx, fy)
        if prev is None:
            return None
        dx, dy = fx - prev[0], fy - prev[1]
        if math.hypot(dx, dy) < self.config.deadzone:
            return None
        gain = self.config.gain_px
        if self._precision:
            gain *= self.config.precision_scale
        return -dx * gain, dy * gain

    def set_precision(self, on: bool) -> None:
        """Scale gain by config.precision_scale while on (aim/pixel work)."""
        self._precision = on

    def reset(self) -> None:
        """Forget filters and last position (tracking lost or control toggled)."""
        self._fx.reset()
        self._fy.reset()
        self._prev = None
