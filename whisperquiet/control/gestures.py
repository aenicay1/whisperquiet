"""Facial-gesture engine (DESIGN.md decision #8): blendshapes → control events.

Pure Python on plain dicts — no mediapipe/opencv/AppKit — so tests run without
a camera. Time comes only from the caller's `t` (monotonic seconds), never the
wall clock, so tests are deterministic.

Detection rules:
- Wink = one eye's blink score high while the other stays low; both eyes high
  is a natural blink and must never fire. The click fires on RELEASE so a long
  deliberate eye-closure (>= max duration) can be cancelled.
- A left-wink click landing within double_wink_window of the previous one is
  emitted as DOUBLE_CLICK instead of a second LEFT_CLICK. The window is
  measured from the last click, and a double resets the chain, so a quick
  triple is LEFT_CLICK, DOUBLE_CLICK, LEFT_CLICK. Right winks are unaffected.
- Scroll gestures (browInnerUp / mouthPucker) repeat while held: first event
  after the hold time, then every repeat interval. The interval ramps
  linearly from scroll_repeat down to scroll_repeat_min over ~2s of hold;
  releasing resets the ramp.
- jawOpen held fires DRAG_START once (open-mouth-hold = drag, decision #8);
  releasing below jaw_off then fires DRAG_END. The refractory window blocks
  starting a new drag, never ending one. TOGGLE_DICTATION stays in the enum:
  the integrator maps DRAG_START to dictation-toggle when configured.
- cheekPuff held fires PAUSE_TOGGLE once per hold (jaw-style machine, no end
  event). Its machine is independent of all the others so the integrator can
  keep detecting the puff — and un-pause — while it pauses everything else.
- All scores are evaluated relative to a neutral-face baseline
  (set_baseline) as max(0, score - baseline); faces differ a lot at rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class GestureEvent(Enum):
    LEFT_CLICK = "left_click"
    DOUBLE_CLICK = "double_click"  # second left click within double_wink_window
    RIGHT_CLICK = "right_click"
    SCROLL_UP = "scroll_up"  # repeating while held
    SCROLL_DOWN = "scroll_down"  # repeating while held
    TOGGLE_DICTATION = "toggle_dictation"
    DRAG_START = "drag_start"
    DRAG_END = "drag_end"
    PAUSE_TOGGLE = "pause_toggle"  # cheek puff: pause/resume gesture control


@dataclass
class GestureConfig:
    # Winks: on/off hysteresis, max score allowed on the opposite eye, and a
    # duration cap (longer = eyes just closed, not a wink).
    wink_on: float = 0.6
    wink_off: float = 0.4
    wink_opposite_max: float = 0.3
    wink_max_duration: float = 0.8
    # Left-wink click within this window of the previous click = DOUBLE_CLICK.
    double_wink_window: float = 0.6
    # Scrolls (browInnerUp / mouthPucker): hold before the first event, then
    # repeat on an interval that ramps from scroll_repeat down to
    # scroll_repeat_min over ~2s while held.
    scroll_on: float = 0.5
    scroll_off: float = 0.35
    scroll_hold: float = 0.15
    scroll_repeat: float = 0.15
    scroll_repeat_min: float = 0.05
    # jawOpen drag: must be held to start; refractory blocks a new start.
    jaw_on: float = 0.6
    jaw_off: float = 0.4
    jaw_hold: float = 0.4
    jaw_refractory: float = 1.0
    # cheekPuff pause toggle: same hold-to-fire shape as the jaw drag.
    puff_on: float = 0.5
    puff_off: float = 0.35
    puff_hold: float = 0.3
    puff_refractory: float = 1.0
    # Per-gesture overrides set by the calibration wizard; None falls back
    # to the shared wink/scroll values above.
    wink_on_left: float | None = None
    wink_off_left: float | None = None
    wink_on_right: float | None = None
    wink_off_right: float | None = None
    brow_on: float | None = None
    brow_off: float | None = None
    pucker_on: float | None = None
    pucker_off: float | None = None


@dataclass
class _WinkState:
    phase: str = "idle"  # idle | winking | cancelled
    start: float = 0.0


@dataclass
class _RepeatState:
    held: bool = False
    start: float = 0.0
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
        self._puff = _HoldState()
        self._last_left_click: float = float("-inf")

    @property
    def winking(self) -> bool:
        """True while either eye is mid-wink (the integrator slows the cursor
        during aim)."""
        return (
            self._left_wink.phase == "winking" or self._right_wink.phase == "winking"
        )

    def set_baseline(self, blendshapes: dict[str, float]) -> None:
        """Store neutral-face scores; process() works on max(0, score - baseline)."""
        self._baseline = dict(blendshapes)

    def process(self, blendshapes: dict[str, float], t: float) -> None:
        score = lambda key: max(  # noqa: E731
            0.0, blendshapes.get(key, 0.0) - self._baseline.get(key, 0.0)
        )
        cfg = self.config
        left, right = score("eyeBlinkLeft"), score("eyeBlinkRight")
        self._step_wink(
            self._left_wink, left, right, t, GestureEvent.LEFT_CLICK,
            cfg.wink_on_left, cfg.wink_off_left,
        )
        self._step_wink(
            self._right_wink, right, left, t, GestureEvent.RIGHT_CLICK,
            cfg.wink_on_right, cfg.wink_off_right,
        )
        self._step_repeat(
            self._brow, score("browInnerUp"), t, GestureEvent.SCROLL_UP,
            cfg.brow_on, cfg.brow_off,
        )
        self._step_repeat(
            self._pucker, score("mouthPucker"), t, GestureEvent.SCROLL_DOWN,
            cfg.pucker_on, cfg.pucker_off,
        )
        self._step_jaw(score("jawOpen"), t)
        self._step_puff(score("cheekPuff"), t)

    def _step_wink(
        self,
        s: _WinkState,
        own: float,
        other: float,
        t: float,
        event: GestureEvent,
        on: float | None = None,
        off: float | None = None,
    ) -> None:
        cfg = self.config
        on = cfg.wink_on if on is None else on
        off = cfg.wink_off if off is None else off
        if s.phase == "idle":
            if own >= on:
                if other <= cfg.wink_opposite_max:
                    s.phase, s.start = "winking", t
                else:  # both eyes high = natural blink; suppress until release
                    s.phase = "cancelled"
        elif s.phase == "winking":
            if other > cfg.wink_opposite_max:
                s.phase = "cancelled"
            elif own < off:
                s.phase = "idle"
                if t - s.start < cfg.wink_max_duration:
                    self._fire_click(event, t)
        elif s.phase == "cancelled" and own < off:
            s.phase = "idle"

    def _fire_click(self, event: GestureEvent, t: float) -> None:
        """A LEFT_CLICK within double_wink_window of the previous click becomes
        DOUBLE_CLICK; the double resets the chain so the window is always
        measured from the last plain click. Right clicks pass straight through."""
        if event is GestureEvent.LEFT_CLICK:
            if t - self._last_left_click <= self.config.double_wink_window:
                self._last_left_click = float("-inf")
                event = GestureEvent.DOUBLE_CLICK
            else:
                self._last_left_click = t
        self.on_event(event)

    def _step_repeat(
        self,
        s: _RepeatState,
        own: float,
        t: float,
        event: GestureEvent,
        on: float | None = None,
        off: float | None = None,
    ) -> None:
        cfg = self.config
        on = cfg.scroll_on if on is None else on
        off = cfg.scroll_off if off is None else off
        if not s.held:
            if own >= on:
                s.held, s.start, s.next_fire = True, t, t + cfg.scroll_hold
        elif own < off:
            s.held = False
        elif t >= s.next_fire:
            self.on_event(event)
            # Acceleration: linear ramp to the minimum interval after ~2s held.
            interval = max(
                cfg.scroll_repeat_min,
                cfg.scroll_repeat
                - (cfg.scroll_repeat - cfg.scroll_repeat_min) * (t - s.start) / 2.0,
            )
            s.next_fire = t + interval

    def _step_jaw(self, own: float, t: float) -> None:
        cfg, s = self.config, self._jaw
        if not s.held:
            if own >= cfg.jaw_on:
                s.held, s.start, s.fired = True, t, False
        elif own < cfg.jaw_off:
            s.held = False
            if s.fired:  # drag in progress: always end it, no refractory
                s.fired = False
                self.on_event(GestureEvent.DRAG_END)
        if (
            s.held
            and not s.fired
            and t - s.start >= cfg.jaw_hold
            and t - s.last_fire >= cfg.jaw_refractory
        ):
            self.on_event(GestureEvent.DRAG_START)
            s.fired, s.last_fire = True, t

    def _step_puff(self, own: float, t: float) -> None:
        cfg, s = self.config, self._puff
        if not s.held:
            if own >= cfg.puff_on:
                s.held, s.start, s.fired = True, t, False
        elif own < cfg.puff_off:
            s.held = False
        if (
            s.held
            and not s.fired
            and t - s.start >= cfg.puff_hold
            and t - s.last_fire >= cfg.puff_refractory
        ):
            self.on_event(GestureEvent.PAUSE_TOGGLE)
            s.fired, s.last_fire = True, t
