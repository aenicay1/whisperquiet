from whisperquiet.control.gestures import GestureConfig, GestureEngine, GestureEvent

FPS_DT = 1 / 30


def make_engine():
    events: list[GestureEvent] = []
    return GestureEngine(events.append), events


def run(engine, frames):
    """frames: iterable of (t, blendshapes)."""
    for t, shapes in frames:
        engine.process(shapes, t)


def hold(key, value, start, duration, dt=FPS_DT, extra=None):
    """Frames with `key` at `value` for `duration`, then one release frame."""
    i = 0
    while i * dt < duration:
        yield start + i * dt, {key: value, **(extra or {})}
        i += 1
    yield start + i * dt, {key: 0.0, **(extra or {})}


def test_left_wink_fires_one_left_click_on_release():
    engine, events = make_engine()
    engine.process({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}, 0.0)
    assert events == []  # fires on release, not on press
    engine.process({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}, 0.2)
    engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}, 0.3)
    assert events == [GestureEvent.LEFT_CLICK]


def test_right_wink_fires_right_click():
    engine, events = make_engine()
    engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.8}, 0.0)
    engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}, 0.2)
    assert events == [GestureEvent.RIGHT_CLICK]


def test_natural_blink_fires_nothing():
    engine, events = make_engine()
    engine.process({"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9}, 0.0)
    engine.process({"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9}, 0.1)
    engine.process({"eyeBlinkLeft": 0.0, "eyeBlinkRight": 0.0}, 0.2)
    assert events == []


def test_wink_cancelled_when_other_eye_closes_midway():
    engine, events = make_engine()
    engine.process({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}, 0.0)
    engine.process({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.8}, 0.1)  # blink lands
    engine.process({"eyeBlinkLeft": 0.0, "eyeBlinkRight": 0.0}, 0.2)
    assert events == []


def test_long_eye_closure_fires_nothing():
    engine, events = make_engine()
    run(engine, hold("eyeBlinkLeft", 0.9, start=0.0, duration=1.5))
    assert events == []


def test_brow_held_emits_scroll_up_at_repeat_cadence():
    engine, events = make_engine()
    run(engine, hold("browInnerUp", 0.8, start=0.0, duration=1.0, dt=0.01))
    # first event after the 0.15s hold, then every 0.15s: 0.15..0.90 -> 6
    # events (frame quantization can shift each fire by at most one 0.01s
    # frame, which cannot change the count within the 1.0s window).
    assert events == [GestureEvent.SCROLL_UP] * 6


def test_brow_released_before_hold_time_fires_nothing():
    engine, events = make_engine()
    engine.process({"browInnerUp": 0.8}, 0.0)
    engine.process({"browInnerUp": 0.8}, 0.1)
    engine.process({"browInnerUp": 0.0}, 0.14)
    assert events == []


def test_pucker_held_emits_scroll_down():
    engine, events = make_engine()
    run(engine, hold("mouthPucker", 0.8, start=0.0, duration=0.5, dt=0.05))
    assert events
    assert set(events) == {GestureEvent.SCROLL_DOWN}


def test_jaw_hold_fires_drag_start_once_then_drag_end_on_release():
    engine, events = make_engine()
    run(engine, hold("jawOpen", 0.9, start=0.0, duration=3.0))  # well past refractory
    assert events == [GestureEvent.DRAG_START, GestureEvent.DRAG_END]
    # extra relaxed frames: DRAG_END fires exactly once.
    engine.process({"jawOpen": 0.0}, 3.2)
    engine.process({"jawOpen": 0.0}, 3.3)
    assert events == [GestureEvent.DRAG_START, GestureEvent.DRAG_END]


def test_jaw_short_tap_fires_nothing():
    engine, events = make_engine()
    run(engine, hold("jawOpen", 0.9, start=0.0, duration=0.2))
    assert events == []


def test_jaw_refractory_blocks_rapid_second_drag_start():
    engine, events = make_engine()
    run(engine, hold("jawOpen", 0.9, start=0.0, duration=0.5))  # start ~0.4, end ~0.5
    assert events == [GestureEvent.DRAG_START, GestureEvent.DRAG_END]
    # re-hold immediately: hold time met at ~1.0s but refractory (started
    # ~0.4s) runs to ~1.4s — no second DRAG_START before the cooldown.
    run(engine, hold("jawOpen", 0.9, start=0.6, duration=0.7))
    assert events == [GestureEvent.DRAG_START, GestureEvent.DRAG_END]
    # held past the cooldown: the second drag starts and ends normally.
    run(engine, hold("jawOpen", 0.9, start=1.4, duration=1.0))
    assert events == [
        GestureEvent.DRAG_START,
        GestureEvent.DRAG_END,
        GestureEvent.DRAG_START,
        GestureEvent.DRAG_END,
    ]


def test_winking_property_tracks_wink_phase():
    engine, _ = make_engine()
    assert engine.winking is False
    engine.process({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}, 0.0)
    assert engine.winking is True
    engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}, 0.2)
    assert engine.winking is False
    # natural blink (both eyes) is cancelled, not winking.
    engine.process({"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9}, 0.4)
    assert engine.winking is False


def test_baseline_subtraction_prevents_resting_brow_autoscroll():
    engine, events = make_engine()
    engine.set_baseline({"browInnerUp": 0.4})
    run(engine, hold("browInnerUp", 0.55, start=0.0, duration=1.0))
    assert events == []  # 0.55 - 0.4 = 0.15, below scroll_on
    run(engine, hold("browInnerUp", 0.95, start=2.0, duration=0.5))
    assert GestureEvent.SCROLL_UP in events  # a real raise still scrolls


def test_missing_keys_default_to_zero():
    engine, events = make_engine()
    engine.set_baseline({})
    engine.process({}, 0.0)
    engine.process({}, 1.0)
    assert events == []


def test_hysteresis_oscillation_between_thresholds_does_not_retrigger():
    engine, events = make_engine()
    # Full wink and release -> one click.
    engine.process({"eyeBlinkLeft": 0.7}, 0.0)
    engine.process({"eyeBlinkLeft": 0.1}, 0.1)
    assert events == [GestureEvent.LEFT_CLICK]
    # Score then oscillates between off (0.4) and on (0.6): never re-arms.
    for i, v in enumerate([0.5, 0.45, 0.55, 0.5, 0.45, 0.1]):
        engine.process({"eyeBlinkLeft": v}, 0.2 + i * FPS_DT)
    assert events == [GestureEvent.LEFT_CLICK]


def test_hysteresis_dip_below_on_but_above_off_keeps_scroll_held():
    # Once held, dipping to 0.4 (>= off 0.35, < on 0.5) must not reset the
    # hold timer — repeats keep coming.
    engine, events = make_engine()
    engine.process({"browInnerUp": 0.8}, 0.0)
    engine.process({"browInnerUp": 0.4}, 0.1)
    engine.process({"browInnerUp": 0.4}, 0.2)  # past hold time, still held
    assert events == [GestureEvent.SCROLL_UP]


def test_custom_config_thresholds_respected():
    events: list[GestureEvent] = []
    cfg = GestureConfig(wink_on=0.9, wink_off=0.8)
    engine = GestureEngine(events.append, config=cfg)
    engine.process({"eyeBlinkLeft": 0.7}, 0.0)  # below custom on-threshold
    engine.process({"eyeBlinkLeft": 0.0}, 0.1)
    assert events == []
