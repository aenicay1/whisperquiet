"""Unit tests for whisperquiet.hotkey.PushToTalk's Quartz event-tap callback.

These tests call `PushToTalk._handle` directly (bypassing `start`/tap
creation, which requires a real macOS session with Input Monitoring granted)
and fake out the handful of Quartz accessor functions the callback reads
event data through. `Quartz` itself is the real pyobjc-framework-Quartz
module (available in .venv on macOS) so the flag constants used below
(kCGEventFlagsChanged, kCGEventKeyDown, ...) are the genuine values.
"""

from __future__ import annotations

import Quartz
import pytest

from whisperquiet import hotkey as hotkey_mod
from whisperquiet.hotkey import KEYCODES, PushToTalk

LETTER_A_KEYCODE = 0  # arbitrary "ordinary" key, distinct from any modifier


class FakeEvent:
    """Stand-in for a CGEventRef; carries just the fields _handle reads."""

    def __init__(self, keycode=0, autorepeat=False, flags=0, source_user_data=0):
        self.keycode = keycode
        self.autorepeat = autorepeat
        self.flags = flags
        self.source_user_data = source_user_data


class FakeClock:
    """Deterministic stand-in for time.monotonic()."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture()
def clock(monkeypatch):
    fc = FakeClock()
    monkeypatch.setattr(hotkey_mod.time, "monotonic", fc)
    return fc


@pytest.fixture(autouse=True)
def patch_quartz_accessors(monkeypatch):
    """Replace the Quartz event-field getters with ones that read FakeEvent."""

    def fake_int_field(event, field):
        if field == Quartz.kCGEventSourceUserData:
            return event.source_user_data
        if field == Quartz.kCGKeyboardEventKeycode:
            return event.keycode
        if field == Quartz.kCGKeyboardEventAutorepeat:
            return event.autorepeat
        raise AssertionError(f"unexpected field {field!r}")

    def fake_get_flags(event):
        return event.flags

    monkeypatch.setattr(Quartz, "CGEventGetIntegerValueField", fake_int_field)
    monkeypatch.setattr(Quartz, "CGEventGetFlags", fake_get_flags)


def make_ptt(**kwargs):
    on_press = kwargs.pop("on_press", None) or _Counter()
    on_release = kwargs.pop("on_release", None) or _Counter()
    on_flag = kwargs.pop("on_flag", None) or _Counter()
    ptt = PushToTalk(
        key_name=kwargs.pop("key_name", "alt_r"),
        on_press=on_press,
        on_release=on_release,
        flag_key_name=kwargs.pop("flag_key_name", "shift_r"),
        on_flag=on_flag,
        **kwargs,
    )
    ptt.on_press_counter = on_press
    ptt.on_release_counter = on_release
    ptt.on_flag_counter = on_flag
    return ptt


class _Counter:
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1


SHIFT_MASK = Quartz.kCGEventFlagMaskShift
ALT_MASK = Quartz.kCGEventFlagMaskAlternate
SHIFT_R_KEYCODE = KEYCODES["shift_r"]
ALT_R_KEYCODE = KEYCODES["alt_r"]
F13_KEYCODE = KEYCODES["f13"]


def press_flag(ptt):
    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=SHIFT_R_KEYCODE, flags=SHIFT_MASK,
    ), None)


def release_flag(ptt):
    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=SHIFT_R_KEYCODE, flags=0,
    ), None)


def key_down(ptt, keycode, autorepeat=False):
    ptt._handle(None, Quartz.kCGEventKeyDown, FakeEvent(
        keycode=keycode, autorepeat=autorepeat,
    ), None)


def key_up(ptt, keycode):
    ptt._handle(None, Quartz.kCGEventKeyUp, FakeEvent(keycode=keycode), None)


def test_isolated_quick_tap_fires_exactly_once_on_release(clock):
    ptt = make_ptt()

    press_flag(ptt)
    assert ptt.on_flag_counter.count == 0  # must not fire on press
    assert ptt._flag_held is True

    clock.advance(0.1)
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 1
    assert ptt._flag_held is False


def test_shift_plus_letter_does_not_fire(clock):
    """Holding shift to type a capital letter must not flag."""
    ptt = make_ptt()

    press_flag(ptt)
    key_down(ptt, LETTER_A_KEYCODE)
    key_up(ptt, LETTER_A_KEYCODE)
    clock.advance(0.05)
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 0
    assert ptt._flag_held is False


def test_long_hold_does_not_fire(clock):
    ptt = make_ptt()

    press_flag(ptt)
    clock.advance(0.6)  # exceeds the 0.5s tap threshold
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 0
    assert ptt._flag_held is False


def test_hold_exactly_at_threshold_does_not_fire(clock):
    ptt = make_ptt()

    press_flag(ptt)
    clock.advance(0.5)  # not strictly < 0.5s
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 0


def test_two_isolated_taps_fire_twice(clock):
    ptt = make_ptt()

    press_flag(ptt)
    clock.advance(0.1)
    release_flag(ptt)

    clock.advance(1.0)  # plenty of idle time between taps

    press_flag(ptt)
    clock.advance(0.05)
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 2


def test_ptt_keydown_activity_during_flag_hold_suppresses_flag(clock):
    """PTT key implemented as an ordinary key (keyDown/keyUp), per spec."""
    ptt = make_ptt(key_name="f13")

    press_flag(ptt)
    key_down(ptt, F13_KEYCODE)
    assert ptt.on_press_counter.count == 1  # PTT press still works normally
    key_up(ptt, F13_KEYCODE)
    assert ptt.on_release_counter.count == 1  # PTT release still works normally

    clock.advance(0.05)
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 0


def test_ptt_modifier_activity_during_flag_hold_suppresses_flag(clock):
    """PTT key implemented as a modifier (flagsChanged), per spec."""
    ptt = make_ptt(key_name="alt_r")

    press_flag(ptt)
    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=ALT_R_KEYCODE, flags=SHIFT_MASK | ALT_MASK,
    ), None)
    assert ptt.on_press_counter.count == 1  # PTT press still works normally

    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=ALT_R_KEYCODE, flags=SHIFT_MASK,
    ), None)
    assert ptt.on_release_counter.count == 1  # PTT release still works normally

    clock.advance(0.05)
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 0


def test_ptt_press_without_flag_held_still_fires_normally(clock):
    """Sanity check: PTT handling is untouched when the flag key isn't involved."""
    ptt = make_ptt(key_name="f13")

    key_down(ptt, F13_KEYCODE)
    assert ptt.on_press_counter.count == 1
    key_up(ptt, F13_KEYCODE)
    assert ptt.on_release_counter.count == 1


def test_unrelated_modifier_during_flag_hold_suppresses_flag(clock):
    """A different modifier (e.g. cmd for a shortcut) while flag is held
    counts as 'used as modifier', per spec bullet (a)."""
    ptt = make_ptt()
    cmd_r_keycode = KEYCODES["cmd_r"]

    press_flag(ptt)
    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=cmd_r_keycode, flags=SHIFT_MASK | Quartz.kCGEventFlagMaskCommand,
    ), None)
    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=cmd_r_keycode, flags=SHIFT_MASK,
    ), None)

    clock.advance(0.05)
    release_flag(ptt)

    assert ptt.on_flag_counter.count == 0


def test_flag_key_none_is_a_noop(clock):
    """Flag feature disabled (no flag_key configured) must never crash or fire."""
    on_flag = _Counter()
    ptt = PushToTalk(
        key_name="alt_r",
        on_press=_Counter(),
        on_release=_Counter(),
        flag_key_name=None,
        on_flag=on_flag,
    )
    key_down(ptt, LETTER_A_KEYCODE)
    ptt._handle(None, Quartz.kCGEventFlagsChanged, FakeEvent(
        keycode=ALT_R_KEYCODE, flags=ALT_MASK,
    ), None)
    assert ptt._flag_held is False
    assert on_flag.count == 0
