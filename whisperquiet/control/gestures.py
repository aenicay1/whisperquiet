"""Facial-gesture engine (DESIGN.md decision #8): blendshapes → control events.

Pure Python on plain dicts — no mediapipe/opencv/AppKit — so tests run without
a camera. Time comes only from the caller's `t` (monotonic seconds), never the
wall clock, so tests are deterministic.

Detection rules:
- Wink = one eye's blink score high while the other stays low; both eyes high
  is a natural blink and must never fire. The click fires on RELEASE so a long
  deliberate eye-closure (>= max duration) can be cancelled.
- Scroll gestures (browInnerUp / mouthPucker) repeat while held: first event
  after the hold time, then every repeat interval.
- jawOpen held fires TOGGLE_DICTATION once; a refractory window blocks
  re-fire until the jaw is released and the cooldown has passed.
- All scores are evaluated relative to a neutral-face baseline
  (set_baseline) as max(0, score - baseline); faces differ a lot at rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class GestureEvent(Enum):
    LEFT_CLICK = "left_click"
    RIGHT_CLICK = "right_click"
    SCROLL_UP = "scroll_up"  # repeating while held
    SCROLL_DOWN = "scroll_down"  # repeating while held
    TOGGLE_DICTATION = "toggle_dictation"


@dataclass
class GestureConfig:
    # Winks: on/off hysteresis, max score allowed on the opposite eye, and a
    # duration cap (longer = eyes just closed, not a wink).
    wink_on: float = 0.6
    wink_off: float = 0.4
    wink_opposite_max: float = 0.3
    wink_max_duration: float = 0.8
    # Scrolls (browInnerUp / mouthPucker): hold before the first event, then
    # repeat on an interval while held.
    scroll_on: float = 0.5
    scroll_off: float = 0.35
    scroll_hold: float = 0.15
    scroll_repeat: float = 0.15
    # jawOpen toggle: must be held, then refractory blocks re-fire.
    jaw_on: float = 0.6
    jaw_off: float = 0.4
    jaw_hold: float = 0.4
    jaw_refractory: float = 1.0


@dataclass
class _WinkState:
    phase: str = "idle"  # idle | winking | cancelled
    start: float = 0.0


@dataclass
class _RepeatState:
    held: bool = False
    next_fire: float = 0.0


@dataclass
class _HoldState:
    held: bool = False
    start: float = 0.0
    fired: bool = False
    last_fire: float = field(default=float("-inf"))


class GestureEngine:
    def __init__(
        self,
        on_event: Callable[[GestureEvent], None],
        config: GestureConfig | None = None,
    ) -> None:
        self.on_event = on_event
        self.config = config or GestureConfig()
        self._baseline: dict[str, float] = {}
        self._left_wink = _WinkState()
        self._right_wink = _WinkState()
        self._brow = _RepeatState()
        self._pucker = _RepeatState()
        self._jaw = _HoldState()

    def set_baseline(self, blendshapes: dict[str, float]) -> None:
        """Store neutral-face scores; process() works on max(0, score - baseline)."""
        self._baseline = dict(blendshapes)

    def process(self, blendshapes: dict[str, float], t: float) -> None:
        score = lambda key: max(  # noqa: E731
            0.0, blendshapes.get(key, 0.0) - self._baseline.get(key, 0.0)
        )
        left, right = score("eyeBlinkLeft"), score("eyeBlinkRight")
        self._step_wink(self._left_wink, left, right, t, GestureEvent.LEFT_CLICK)
        self._step_wink(self._right_wink, right, left, t, GestureEvent.RIGHT_CLICK)
        self._step_repeat(self._brow, score("browInnerUp"), t, GestureEvent.SCROLL_UP)
        self._step_repeat(
            self._pucker, score("mouthPucker"), t, GestureEvent.SCROLL_DOWN
        )
        self._step_jaw(score("jawOpen"), t)

    def _step_wink(
        self, s: _WinkState, own: float, other: float, t: float, event: GestureEvent
    ) -> None:
        cfg = self.config
        if s.phase == "idle":
            if own >= cfg.wink_on:
                if other <= cfg.wink_opposite_max:
                    s.phase, s.start = "winking", t
                else:  # both eyes high = natural blink; suppress until release
                    s.phase = "cancelled"
        elif s.phase == "winking":
            if other > cfg.wink_opposite_max:
                s.phase = "cancelled"
            elif own < cfg.wink_off:
                s.phase = "idle"
                if t - s.start < cfg.wink_max_duration:
                    self.on_event(event)
        elif s.phase == "cancelled" and own < cfg.wink_off:
            s.phase = "idle"

    def _step_repeat(
        self, s: _RepeatState, own: float, t: float, event: GestureEvent
    ) -> None:
        cfg = self.config
        if not s.held:
            if own >= cfg.scroll_on:
                s.held, s.next_fire = True, t + cfg.scroll_hold
        elif own < cfg.scroll_off:
            s.held = False
        elif t >= s.next_fire:
            self.on_event(event)
            s.next_fire = t + cfg.scroll_repeat

    def _step_jaw(self, own: float, t: float) -> None:
        cfg, s = self.config, self._jaw
        if not s.held:
            if own >= cfg.jaw_on:
                s.held, s.start, s.fired = True, t, False
        elif own < cfg.jaw_off:
            s.held = False
        if (
            s.held
            and not s.fired
            and t - s.start >= cfg.jaw_hold
            and t - s.last_fire >= cfg.jaw_refractory
        ):
            self.on_event(GestureEvent.TOGGLE_DICTATION)
            s.fired, s.last_fire = True, t
