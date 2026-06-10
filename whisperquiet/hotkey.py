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
    ) -> None:
        self._keycode = KEYCODES.get(key_name, KEYCODES["alt_r"])
        self._on_press = on_press
        self._on_release = on_release
        self._held = False
        self._tap = None

    def start(self) -> None:
        AppHelper.callAfter(self._install_main)

    def _install_main(self) -> None:
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
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
                "System Settings → Privacy & Security, then relaunch."
            )
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(self._tap, True)

    def _handle(self, proxy, etype, event, refcon):
        try:
            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )
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
