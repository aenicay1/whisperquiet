"""Commit final text into the focused app (DESIGN.md decision #10).

Default mode posts unicode keyboard events via Quartz, which works in most
apps without touching the clipboard. Paste mode is the fallback for apps that
drop synthetic unicode events; it preserves and restores the user's clipboard.
Both require the Accessibility permission.
"""

from __future__ import annotations

import time

import AppKit
import Quartz

_CHUNK = 20  # CGEventKeyboardSetUnicodeString caps around 20 UTF-16 units
SYNTHETIC_TAG = 0x57510001  # marks our events so the feedback tap ignores them


def _post(event) -> None:
    Quartz.CGEventSetIntegerValueField(
        event, Quartz.kCGEventSourceUserData, SYNTHETIC_TAG
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

# macOS virtual keycodes (Carbon HIToolbox Events.h).
KEY_RETURN = 36
KEY_ESCAPE = 53


def press_key(keycode: int) -> None:
    """Press and release one key by its real keycode. Return/Escape must go
    through actual keycodes — apps match on them, not on unicode strings."""
    for key_down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, keycode, key_down)
        _post(event)


def type_text(text: str, mode: str = "keystrokes") -> None:
    if not text:
        return
    if mode == "paste":
        _paste(text)
    else:
        _keystrokes(text)


def _keystrokes(text: str) -> None:
    for i in range(0, len(text), _CHUNK):
        chunk = text[i : i + _CHUNK]
        for key_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, key_down)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
            _post(event)
        time.sleep(0.005)


def _paste(text: str) -> None:
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    saved = pasteboard.stringForType_(AppKit.NSPasteboardTypeString)
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)

    v_key, cmd_flag = 9, Quartz.kCGEventFlagMaskCommand
    for key_down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, v_key, key_down)
        Quartz.CGEventSetFlags(event, cmd_flag)
        _post(event)

    time.sleep(0.15)  # let the target app read the pasteboard before restoring
    if saved is not None:
        pasteboard.clearContents()
        pasteboard.setString_forType_(saved, AppKit.NSPasteboardTypeString)
