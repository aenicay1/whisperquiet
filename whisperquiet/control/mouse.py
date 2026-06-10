"""Mouse output via Quartz events. Requires the Accessibility permission."""

from __future__ import annotations

import Quartz

_BUTTONS = {
    "left": (
        Quartz.kCGEventLeftMouseDown,
        Quartz.kCGEventLeftMouseUp,
        Quartz.kCGMouseButtonLeft,
    ),
    "right": (
        Quartz.kCGEventRightMouseDown,
        Quartz.kCGEventRightMouseUp,
        Quartz.kCGMouseButtonRight,
    ),
}


def position() -> tuple[float, float]:
    event = Quartz.CGEventCreate(None)
    point = Quartz.CGEventGetLocation(event)
    return point.x, point.y


def move(x: float, y: float) -> None:
    event = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, (x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def click(button: str = "left") -> None:
    down, up, btn = _BUTTONS[button]
    pos = position()
    for kind in (down, up):
        event = Quartz.CGEventCreateMouseEvent(None, kind, pos, btn)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def scroll(dy: int) -> None:
    """Positive dy scrolls content up (wheel-up), negative down."""
    event = Quartz.CGEventCreateScrollWheelEvent(
        None, Quartz.kCGScrollEventUnitLine, 1, dy
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
