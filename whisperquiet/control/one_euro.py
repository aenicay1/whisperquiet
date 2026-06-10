"""One-Euro filter (Casiez et al. 2012) — jitter smoothing for the head cursor.

Low speed → heavy smoothing (kills jitter); high speed → light smoothing
(kills lag). The two knobs: min_cutoff trades jitter for lag at rest, beta
trades lag for jitter in motion.
"""

from __future__ import annotations

import math


class OneEuroFilter:
    def __init__(
        self, min_cutoff: float = 1.0, beta: float = 0.007, d_cutoff: float = 1.0
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._prev_x: float | None = None
        self._prev_dx = 0.0
        self._prev_t: float | None = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._prev_x is None or self._prev_t is None or t <= self._prev_t:
            self._prev_x, self._prev_t = x, t
            return x

        dt = t - self._prev_t
        dx = (x - self._prev_x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1.0 - a_d) * self._prev_dx

        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1.0 - a) * self._prev_x

        self._prev_x, self._prev_dx, self._prev_t = x_hat, dx_hat, t
        return x_hat

    def reset(self) -> None:
        self._prev_x = None
        self._prev_dx = 0.0
        self._prev_t = None
