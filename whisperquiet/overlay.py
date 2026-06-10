"""Floating streaming-text overlay.

A non-activating NSPanel so the target app keeps keyboard focus — whisper
hypothesis revisions happen here, never via synthetic backspace in someone
else's text field (DESIGN.md decision #10). All AppKit work is marshalled to
the main thread; public methods are safe to call from worker threads.
"""

from __future__ import annotations

import AppKit
from PyObjCTools import AppHelper

_WIDTH, _HEIGHT, _MARGIN_BOTTOM = 520, 64, 120


class Overlay:
    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._label: AppKit.NSTextField | None = None

    # -- public, thread-safe ------------------------------------------------

    def show(self) -> None:
        AppHelper.callAfter(self._show_main)

    def update(self, text: str) -> None:
        AppHelper.callAfter(self._update_main, text)

    def hide(self) -> None:
        AppHelper.callAfter(self._hide_main)

    # -- main thread only ---------------------------------------------------

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            return
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        rect = AppKit.NSMakeRect(
            screen.origin.x + (screen.size.width - _WIDTH) / 2,
            screen.origin.y + _MARGIN_BOTTOM,
            _WIDTH,
            _HEIGHT,
        )
        style = (
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)

        content = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, _WIDTH, _HEIGHT)
        )
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        content.layer().setCornerRadius_(14.0)

        label = AppKit.NSTextField.wrappingLabelWithString_("")
        label.setFrame_(AppKit.NSMakeRect(16, 8, _WIDTH - 32, _HEIGHT - 16))
        label.setTextColor_(AppKit.NSColor.whiteColor())
        label.setFont_(AppKit.NSFont.systemFontOfSize_(16))
        content.addSubview_(label)

        panel.setContentView_(content)
        self._panel, self._label = panel, label

    def _show_main(self) -> None:
        self._ensure_panel()
        self._label.setStringValue_("…")
        self._panel.orderFrontRegardless()

    def _update_main(self, text: str) -> None:
        if self._label is not None:
            self._label.setStringValue_(text or "…")

    def _hide_main(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
