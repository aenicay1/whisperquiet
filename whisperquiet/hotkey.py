"""Global push-to-talk key via a Quartz event tap on the main run loop.

pynput is unusable here: its listener thread calls TSM input-source APIs off
the main queue, which SIGTRAPs inside app bundles on modern macOS. A
listen-only CGEventTap on the main CFRunLoop avoids that entirely. Requires
the Input Monitoring permission; tap creation returns None without it.
"""

from __future__ import annotations

from collections.abc import Callable

import Quartz
from PyObjCTools import AppHelper

from .inject import SYNTHETIC_TAG

# minimal name → virtual keycode map (extend as needed)
KEYCODES = {
    "alt_r": 61,
    "alt_l": 58,
    "cmd_r": 54,
    "ctrl_r": 62,
    "shift_r": 60,
    "f13": 105,
    "f14": 107,
    "f15": 113,
}
_MODIFIER_FLAGS = {
    61: Quartz.kCGEventFlagMaskAlternate,
    58: Quartz.kCGEventFlagMaskAlternate,
    54: Quartz.kCGEventFlagMaskCommand,
    62: Quartz.kCGEventFlagMaskControl,
    60: Quartz.kCGEventFlagMaskShift,
}


class PushToTalk:
    def __init__(
        self,
        key_name: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        flag_key_name: str | None = None,
        on_flag: Callable[[], None] | None = None,
        on_physical_key: Callable[[], None] | None = None,
        on_physical_mouse: Callable[[], None] | None = None,
    ) -> None:
        self._keycode = KEYCODES.get(key_name, KEYCODES["alt_r"])
        self._on_press = on_press
        self._on_release = on_release
        self._flag_keycode = KEYCODES.get(flag_key_name) if flag_key_name else None
        self._on_flag = on_flag
        self._on_physical_key = on_physical_key
        self._on_physical_mouse = on_physical_mouse
        self._held = False
        self._flag_held = False
        self._tap = None

    def start(self) -> None:
        AppHelper.callAfter(self._install_main)

    def _install_main(self) -> None:
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._handle,
            None,
        )
        if self._tap is None:
            print(
                "PTT tap creation failed — grant Input Monitoring in "
                "System Settings → Privacy & Security, then relaunch.",
                flush=True,
            )
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(self._tap, True)
        print("PTT tap installed (keycode", self._keycode, ")", flush=True)

    _MOUSE_TYPES = (
        Quartz.kCGEventMouseMoved,
        Quartz.kCGEventLeftMouseDown,
        Quartz.kCGEventRightMouseDown,
        Quartz.kCGEventScrollWheel,
    )

    def _handle(self, proxy, etype, event, refcon):
        try:
            if (
                Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGEventSourceUserData
                )
                == SYNTHETIC_TAG
            ):
                return event  # our own output, not user input
            if etype in self._MOUSE_TYPES:
                if self._on_physical_mouse is not None:
                    self._on_physical_mouse()
                return event
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
            if etype == Quartz.kCGEventKeyDown and self._on_physical_key is not None:
                if not Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventAutorepeat
                ):
                    self._on_physical_key()
            if (
                self._flag_keycode is not None
                and keycode == self._flag_keycode
                and etype == Quartz.kCGEventFlagsChanged
            ):
                flag = _MODIFIER_FLAGS.get(self._flag_keycode, 0)
                held = bool(Quartz.CGEventGetFlags(event) & flag)
                if held and not self._flag_held and self._on_flag is not None:
                    self._on_flag()
                self._flag_held = held
            if keycode == self._keycode:
                if etype == Quartz.kCGEventFlagsChanged:
                    flag = _MODIFIER_FLAGS.get(self._keycode, 0)
                    self._set_held(bool(Quartz.CGEventGetFlags(event) & flag))
                elif etype == Quartz.kCGEventKeyDown:
                    if not Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventAutorepeat
                    ):
                        self._set_held(True)
                elif etype == Quartz.kCGEventKeyUp:
                    self._set_held(False)
        except Exception:  # never let an exception kill the tap callback
            pass
        return event

    def _set_held(self, held: bool) -> None:
        if held == self._held:
            return
        self._held = held
        (self._on_press if held else self._on_release)()
