import pytest

from whisperquiet.control.gestures import GestureEngine, GestureEvent
from whisperquiet.vision.calibration import (
    CAPTURE_S,
    READY_S,
    STEPS,
    CalibrationWizard,
    config_from_saved,
)

FPS = 30
DT = 1.0 / FPS

NEUTRAL = {
    "eyeBlinkLeft": 0.08,
    "eyeBlinkRight": 0.08,
    "browInnerUp": 0.20,
    "mouthPucker": 0.10,
    "jawOpen": 0.05,
}
# this user winks weakly on the left, strongly on the right
ACTIVE = {
    "left_wink": {"eyeBlinkLeft": 0.55},
    "right_wink": {"eyeBlinkRight": 0.95},
    "brow": {"browInnerUp": 0.85},
    "pucker": {"mouthPucker": 0.70},
    "jaw": {"jawOpen": 0.90},
}


def run_wizard() -> CalibrationWizard:
    wizard = CalibrationWizard()
    t = 0.0
    for key, _, _ in STEPS:
        frames = int((READY_S + CAPTURE_S) * FPS) + 2
        for _ in range(frames):
            wizard.process({**NEUTRAL, **ACTIVE.get(key, {})}, t)
            t += DT
            if wizard.done:
                return wizard
    assert wizard.done
    return wizard


def test_wizard_completes_and_baselines():
    wizard = run_wizard()
    base = wizard.baseline()
    assert base["browInnerUp"] == pytest.approx(0.20, abs=0.01)


def test_personal_thresholds_track_each_gestures_range():
    config, persisted = run_wizard().build()
    # weak left wink → lower threshold than the strong right wink
    assert config.wink_on_left < config.wink_on_right
    # all on-thresholds sit between off-threshold and the achievable range
    for on, off in [
        (config.wink_on_left, config.wink_off_left),
        (config.wink_on_right, config.wink_off_right),
        (config.brow_on, config.brow_off),
        (config.pucker_on, config.pucker_off),
    ]:
        assert 0 < off < on <= 0.85
    # persistence round-trips
    config2, baseline = config_from_saved(persisted)
    assert config2 == config
    assert baseline["jawOpen"] == pytest.approx(0.05, abs=0.01)


def test_weak_wink_user_can_still_click():
    """A 0.55-score wink misses the 0.6 default threshold but must work
    after calibration."""
    config, _ = run_wizard().build()
    events = []
    engine = GestureEngine(events.append, config)
    engine.set_baseline(NEUTRAL)
    t = 0.0
    for _ in range(15):  # half-second weak left wink
        engine.process({**NEUTRAL, "eyeBlinkLeft": 0.55}, t)
        t += DT
    for _ in range(15):
        engine.process(dict(NEUTRAL), t)
        t += DT
    assert events == [GestureEvent.LEFT_CLICK]
