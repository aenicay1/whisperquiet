"""Floating streaming-text overlay.

A non-activating NSPanel so the target app keeps keyboard focus — whisper
hypothesis revisions happen here, never via synthetic backspace in someone
else's text field (DESIGN.md decision #10). All AppKit work is marshalled to
the main thread; public methods are safe to call from worker threads.
"""

from __future__ import annotations

import AppKit
from PyObjCTools import AppHelper

_WIDTH, _HEIGHT, _MARGIN_BOTTOM = 520, 96, 120
_TAIL_CHARS = 165  # ~3 lines at 16pt; older text scrolls off the top


class Overlay:
    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._label: AppKit.NSTextField | None = None
        self._last_text = ""

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
        self._last_text = ""
        self._label.setStringValue_("…")
        self._panel.orderFrontRegardless()

    def _update_main(self, text: str) -> None:
        if self._label is None or text == self._last_text:
            return
        self._last_text = text
        shown = text or "…"
        if len(shown) > _TAIL_CHARS:
            # tail-anchored: always show the end of the utterance
            cut = shown[-_TAIL_CHARS:]
            cut = cut.split(" ", 1)[-1] if " " in cut[:30] else cut
            shown = "…" + cut
        self._label.setStringValue_(shown)

    def _hide_main(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)


class NotchIndicator:
    """Small 'listening' pill top-center near the notch with a live mic level,
    so you can see the app is hearing you before any text streams in."""

    _W, _H = 180, 26
    _BAR_X, _BAR_MAX = 100, 68

    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._bar: AppKit.NSView | None = None

    # -- public, thread-safe ------------------------------------------------

    def show(self) -> None:
        AppHelper.callAfter(self._show_main)

    def set_level(self, level: float) -> None:
        AppHelper.callAfter(self._level_main, float(level))

    def hide(self) -> None:
        AppHelper.callAfter(self._hide_main)

    # -- main thread only ---------------------------------------------------

    def _ensure_panel(self) -> None:
        if self._panel is not None:
            return
        visible = AppKit.NSScreen.mainScreen().visibleFrame()
        rect = AppKit.NSMakeRect(
            visible.origin.x + (visible.size.width - self._W) / 2,
            visible.origin.y + visible.size.height - self._H - 6,
            self._W,
            self._H,
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
            AppKit.NSMakeRect(0, 0, self._W, self._H)
        )
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        content.layer().setCornerRadius_(self._H / 2)

        dot = AppKit.NSTextField.labelWithString_("●")
        dot.setFrame_(AppKit.NSMakeRect(12, 4, 14, 18))
        dot.setTextColor_(AppKit.NSColor.systemRedColor())
        dot.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        content.addSubview_(dot)

        text = AppKit.NSTextField.labelWithString_("listening")
        text.setFrame_(AppKit.NSMakeRect(28, 4, 70, 18))
        text.setTextColor_(AppKit.NSColor.whiteColor())
        text.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        content.addSubview_(text)

        bar = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(self._BAR_X, self._H / 2 - 3, 4, 6)
        )
        bar.setWantsLayer_(True)
        bar.layer().setBackgroundColor_(AppKit.NSColor.systemGreenColor().CGColor())
        bar.layer().setCornerRadius_(3.0)
        content.addSubview_(bar)

        panel.setContentView_(content)
        self._panel, self._bar = panel, bar

    def _show_main(self) -> None:
        self._ensure_panel()
        self._level_main(0.0)
        self._panel.orderFrontRegardless()

    def _level_main(self, level: float) -> None:
        if self._bar is not None:
            width = 4 + max(0.0, min(1.0, level)) * (self._BAR_MAX - 4)
            self._bar.setFrame_(
                AppKit.NSMakeRect(self._BAR_X, self._H / 2 - 3, width, 6)
            )

    def _hide_main(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)
