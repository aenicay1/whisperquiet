"""Head-pose nod/shake detection: nose-tip oscillation → discrete gestures.

Pure Python, camera-free — feed the normalized [0,1] position of a tracked
face point (nose tip) each frame. Time comes only from the caller's `t`
(monotonic seconds), never the wall clock, so tests are deterministic.

Detection rules:
- A "reversal" is a velocity sign change on one axis where the swing since
  the previous reversal travelled at least min_amplitude. Tiny jitter never
  reverses a swing; slow unidirectional movement (cursor-style panning)
  never reverses at all, so neither can fire.
- >= reversals_required reversals on the vertical axis within max_gesture_s
  fire "nod"; on the horizontal axis, "shake".
- Axis dominance: the gesturing axis's motion range over the window must
  beat the other axis's by axis_dominance, so diagonal drift fires nothing.
- After a gesture fires, cooldown_s of refractory time blocks everything
  and the swing state restarts fresh.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class NodShakeConfig:
    # Normalized; each swing between reversals must travel at least this far.
    min_amplitude: float = 0.012
    # All required reversals must land within this many seconds.
    max_gesture_s: float = 0.9
    # Qualifying velocity sign changes on one axis needed to fire.
    reversals_required: int = 2
    # Refractory time after a gesture fires.
    cooldown_s: float = 1.0
    # The gesturing axis's motion range must beat the other axis's range by
    # this factor; diagonal drift dominates neither axis and fires nothing.
    axis_dominance: float = 2.0


class _AxisSwings:
    """Zigzag tracker for one axis: timestamps every direction reversal whose
    swing amplitude reaches min_amplitude."""

    def __init__(self, min_amplitude: float) -> None:
        self.min_amplitude = min_amplitude
        self.reversal_times: deque[float] = deque()
        self._anchor: float | None = None  # where the current swing started
        self._extremum = 0.0  # farthest point the current swing reached
        self._direction = 0  # sign of the current swing; 0 = not moving yet

    def feed(self, v: float, t: float) -> None:
        if self._anchor is None:
            self._anchor = self._extremum = v
            return
        if self._direction == 0:
            # Idle until the first swing clears min_amplitude.
            if abs(v - self._anchor) >= self.min_amplitude:
                self._direction = 1 if v > self._anchor else -1
                self._extremum = v
            return
        if (v - self._extremum) * self._direction >= 0:
            self._extremum = v  # swing keeps going; jitter never gets here
        elif abs(v - self._extremum) >= self.min_amplitude:
            # Turned around AND the new swing already qualifies: a reversal.
            self.reversal_times.append(t)
            self._anchor, self._extremum = self._extremum, v
            self._direction = -self._direction

    def prune(self, cutoff: float) -> None:
        while self.reversal_times and self.reversal_times[0] < cutoff:
            self.reversal_times.popleft()

    def reset(self) -> None:
        self.reversal_times.clear()
        self._anchor = None
        self._direction = 0


class NodShakeDetector:
    def __init__(self, config: NodShakeConfig | None = None) -> None:
        self.config = config or NodShakeConfig()
        self._x = _AxisSwings(self.config.min_amplitude)
        self._y = _AxisSwings(self.config.min_amplitude)
        self._history: deque[tuple[float, float, float]] = deque()  # (t, x, y)
        self._cooldown_until: float | None = None

    def process(self, x: float, y: float, t: float) -> str | None:
        """Feed normalized [0,1] nose-tip position per frame; returns "nod",
        "shake", or None."""
        if self._cooldown_until is not None and t < self._cooldown_until:
            return None
        cutoff = t - self.config.max_gesture_s
        self._history.append((t, x, y))
        while self._history[0][0] < cutoff:
            self._history.popleft()
        for axis, v in ((self._x, x), (self._y, y)):
            axis.feed(v, t)
            axis.prune(cutoff)
        for axis, name, gesture in ((self._y, "y", "nod"), (self._x, "x", "shake")):
            if (
                len(axis.reversal_times) >= self.config.reversals_required
                and self._dominates(name)
            ):
                self._cooldown_until = t + self.config.cooldown_s
                self._clear_swings()
                return gesture
        return None

    def _dominates(self, axis: str) -> bool:
        xs = [p[1] for p in self._history]
        ys = [p[2] for p in self._history]
        range_x, range_y = max(xs) - min(xs), max(ys) - min(ys)
        main, other = (range_y, range_x) if axis == "y" else (range_x, range_y)
        return main >= self.config.axis_dominance * other

    def _clear_swings(self) -> None:
        self._x.reset()
        self._y.reset()
        self._history.clear()

    def reset(self) -> None:
        """Forget swings and cooldown (tracking lost or control toggled)."""
        self._clear_swings()
        self._cooldown_until = None
