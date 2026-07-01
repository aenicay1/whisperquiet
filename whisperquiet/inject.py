"""Commit final text into the focused app (DESIGN.md decision #10).

Default mode posts unicode keyboard events via Quartz, which works in most
apps without touching the clipboard. Paste mode is the fallback for apps that
drop synthetic unicode events; it preserves and restores the user's clipboard.
Both require the Accessibility permission.
"""

from __future__ import annotations

import ctypes
import time

import AppKit
import Quartz

_CHUNK = 20  # CGEventKeyboardSetUnicodeString caps around 20 UTF-16 units
SYNTHETIC_TAG = 0x57510001  # marks our events so the feedback tap ignores them

_carbon = None  # lazily-loaded Carbon.framework handle, see _secure_input_enabled


def _secure_input_enabled() -> bool:
    """True while a Secure Input field (e.g. a password box) has focus
    anywhere on the system. macOS blocks synthetic keystrokes AND paste while
    this is on — CGEventPost drops the event with no error — so injecting
    here would look like a successful dictation while typing nothing.

    IsSecureEventInputEnabled() is a plain C symbol in Carbon.framework; it is
    not bridged by PyObjC's Quartz/ApplicationServices modules, so it is
    loaded directly via ctypes. Best-effort: if the framework can't be loaded
    or called, treat secure input as NOT active rather than blocking every
    dictation over a broken check.
    """
    global _carbon
    try:
        if _carbon is None:
            _carbon = ctypes.CDLL(
                "/System/Library/Frameworks/Carbon.framework/Carbon"
            )
            _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False


def can_inject() -> tuple[bool, str | None]:
    """Pre-inject guard for the commit path (app.py's _stream_loop).

    CGEventPost silently drops synthetic keystrokes when the Accessibility
    permission is missing, and macOS blocks them outright while Secure Input
    is active — in both cases nothing is typed and CGEventPost raises
    nothing, so a caller that skips this check would report a successful
    dictation while the focused app received no text. Returns
    ``(True, None)`` when it is safe to inject, else ``(False, reason)`` with
    a short human-readable reason to show on the notch.
    """
    from ApplicationServices import AXIsProcessTrusted

    if not AXIsProcessTrusted():
        return False, "can't type — grant Accessibility in System Settings, then relaunch"
    if _secure_input_enabled():
        return False, "secure input field active — text not typed"
    return True, None


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
