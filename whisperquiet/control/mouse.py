"""Mouse output via Quartz events. Requires the Accessibility permission."""

from __future__ import annotations

import Quartz

# True between left_down() and left_up(); move_by() then posts drag events so
# the OS treats the motion as a drag, not a plain move.
_dragging = False

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


def move_by(dx: float, dy: float) -> None:
    """Move relative to the current position, clamped to the main display.

    Posts a drag event instead of a plain move while a left_down() drag is
    active, so drags track the head cursor.
    """
    x, y = position()
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    nx = min(max(x + dx, bounds.origin.x), bounds.origin.x + bounds.size.width - 1)
    ny = min(max(y + dy, bounds.origin.y), bounds.origin.y + bounds.size.height - 1)
    kind = Quartz.kCGEventLeftMouseDragged if _dragging else Quartz.kCGEventMouseMoved
    event = Quartz.CGEventCreateMouseEvent(
        None, kind, (nx, ny), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def left_down() -> None:
    """Press and hold the left button at the current position (drag start)."""
    global _dragging
    event = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, position(), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    _dragging = True


def left_up() -> None:
    """Release the left button at the current position (drag end)."""
    global _dragging
    _dragging = False
    event = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, position(), Quartz.kCGMouseButtonLeft
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
