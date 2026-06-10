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
    # first event after the 0.15s hold, then accelerating repeats as the
    # interval ramps from 0.15s toward scroll_repeat_min: fires at roughly
    # 0.15/0.30/0.44/0.57/0.70/0.82/0.93 -> 7 events (frame quantization can
    # only delay each fire by one 0.01s frame, which cannot change the count
    # within the 1.0s window).
    assert events == [GestureEvent.SCROLL_UP] * 7


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


def wink_left(engine, start):
    """One left wink: press at `start`, release (the click fires) at start+0.1."""
    engine.process({"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.1}, start)
    engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}, start + 0.1)


def test_two_quick_left_winks_fire_left_click_then_double_click():
    engine, events = make_engine()
    wink_left(engine, 0.0)  # click at 0.1
    wink_left(engine, 0.3)  # click at 0.4, within the 0.6s window
    assert events == [GestureEvent.LEFT_CLICK, GestureEvent.DOUBLE_CLICK]


def test_two_slow_left_winks_fire_two_left_clicks():
    engine, events = make_engine()
    wink_left(engine, 0.0)  # click at 0.1
    wink_left(engine, 1.0)  # click at 1.1, past the 0.6s window
    assert events == [GestureEvent.LEFT_CLICK, GestureEvent.LEFT_CLICK]


def test_triple_quick_wink_double_click_resets_the_chain():
    engine, events = make_engine()
    wink_left(engine, 0.0)  # click at 0.1
    wink_left(engine, 0.3)  # click at 0.4 -> double
    wink_left(engine, 0.6)  # click at 0.7: chain reset, plain click again
    assert events == [
        GestureEvent.LEFT_CLICK,
        GestureEvent.DOUBLE_CLICK,
        GestureEvent.LEFT_CLICK,
    ]


def test_quick_right_winks_never_double_click():
    engine, events = make_engine()
    for start in (0.0, 0.3):
        engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.8}, start)
        engine.process({"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}, start + 0.1)
    assert events == [GestureEvent.RIGHT_CLICK, GestureEvent.RIGHT_CLICK]


def test_cheek_puff_hold_fires_pause_toggle_once():
    engine, events = make_engine()
    run(engine, hold("cheekPuff", 0.8, start=0.0, duration=1.0))
    assert events == [GestureEvent.PAUSE_TOGGLE]


def test_cheek_puff_short_tap_fires_nothing():
    engine, events = make_engine()
    run(engine, hold("cheekPuff", 0.8, start=0.0, duration=0.2))
    assert events == []


def test_cheek_puff_refractory_blocks_rapid_second_toggle():
    engine, events = make_engine()
    run(engine, hold("cheekPuff", 0.8, start=0.0, duration=0.4))  # fires ~0.3
    assert events == [GestureEvent.PAUSE_TOGGLE]
    # re-puff immediately: hold time met at ~0.8s but the refractory (fired
    # ~0.3s) runs to ~1.3s — no second toggle before the cooldown.
    run(engine, hold("cheekPuff", 0.8, start=0.5, duration=0.7))
    assert events == [GestureEvent.PAUSE_TOGGLE]
    # held past the cooldown: the toggle fires again.
    run(engine, hold("cheekPuff", 0.8, start=1.4, duration=0.5))
    assert events == [GestureEvent.PAUSE_TOGGLE] * 2


def test_scroll_acceleration_repeats_denser_later_in_the_hold():
    engine, events = make_engine()

    def feed(t0, t1):
        before = len(events)
        for i in range(int(round((t1 - t0) / 0.01))):
            engine.process({"browInnerUp": 0.8}, t0 + i * 0.01)
        return len(events) - before

    first_half_second = feed(0.0, 0.5)
    feed(0.5, 1.0)
    second_second = feed(1.0, 2.0)  # window twice as long, so compare 2x
    assert first_half_second > 0
    assert second_second > 2 * first_half_second  # denser, not just longer


def test_scroll_release_resets_acceleration():
    engine, events = make_engine()
    run(engine, hold("browInnerUp", 0.8, start=0.0, duration=2.0, dt=0.01))
    after_long_hold = len(events)
    # Fresh hold after release: the ramp restarts, so the first 0.4s gives
    # the slow-cadence 2 events (~0.15/~0.30), not the ~6 a hold still at
    # the minimum interval would.
    run(engine, hold("browInnerUp", 0.8, start=2.5, duration=0.4, dt=0.01))
    assert len(events) - after_long_hold == 2


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
