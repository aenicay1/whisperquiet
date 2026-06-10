"""Floating streaming-text overlay.

A non-activating NSPanel so the target app keeps keyboard focus — whisper
hypothesis revisions happen here, never via synthetic backspace in someone
else's text field (DESIGN.md decision #10). All AppKit work is marshalled to
the main thread; public methods are safe to call from worker threads.

Visuals: frosted-glass NSVisualEffectView panels (HUD material) with soft
fade/rise transitions. Every animation path degrades to an instant
show/hide/swap if the underlying AppKit API is unavailable — never crash
over polish.
"""

from __future__ import annotations

import AppKit
from PyObjCTools import AppHelper

_WIDTH, _HEIGHT, _MARGIN_BOTTOM = 520, 96, 120
_TAIL_CHARS = 165  # ~3 lines at 16pt; older text scrolls off the top
_CORNER_RADIUS = 18.0
_RISE_PX = 8.0
_SHOW_DURATION = 0.15
_HIDE_DURATION = 0.12
_TEXT_FADE_HALF = 0.06  # dip + recover = ~120ms total


# -- module helpers (main thread only) ---------------------------------------


def _rounded_mask_image(radius: float) -> AppKit.NSImage:
    """A stretchable rounded-rect mask image for NSVisualEffectView.

    This is the documented way to clip a behind-window blur to rounded
    corners (a plain CALayer cornerRadius does not reliably clip the blur).
    """
    edge = radius * 2 + 1  # 1px stretchable center

    def draw(rect) -> bool:
        AppKit.NSColor.blackColor().setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, radius, radius
        ).fill()
        return True

    image = AppKit.NSImage.imageWithSize_flipped_drawingHandler_(
        AppKit.NSMakeSize(edge, edge), False, draw
    )
    image.setCapInsets_(AppKit.NSEdgeInsetsMake(radius, radius, radius, radius))
    image.setResizingMode_(AppKit.NSImageResizingModeStretch)
    return image


def _make_glass_panel(rect, radius: float):
    """Create a non-activating, mouse-transparent floating panel with a
    frosted-glass (HUD material) content view clipped to ``radius`` corners.

    Returns ``(panel, content_view)``. Falls back to the flat dark CALayer
    look if NSVisualEffectView (or any of its knobs) is unavailable.
    """
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

    bounds = AppKit.NSMakeRect(0, 0, rect.size.width, rect.size.height)

    content = None
    try:
        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(bounds)
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        try:
            effect.setEmphasized_(True)
        except Exception:
            pass  # pre-10.14 — emphasis is cosmetic only
        try:
            effect.setMaskImage_(_rounded_mask_image(radius))
        except Exception:
            # mask image failed: clip via layer instead (good enough)
            effect.setWantsLayer_(True)
            effect.layer().setCornerRadius_(radius)
            effect.layer().setMasksToBounds_(True)
        content = effect
    except Exception:
        content = None

    if content is None:
        # fallback: the original flat dark layer
        content = AppKit.NSView.alloc().initWithFrame_(bounds)
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        content.layer().setCornerRadius_(radius)

    panel.setContentView_(content)
    return panel, content


def _fade_in_panel(panel, end_frame, rise: float = _RISE_PX) -> None:
    """Order the panel front with a fade (alpha 0→1) and a small upward rise.

    Degrades to an instant orderFront if animation APIs misbehave.
    """
    try:
        start_frame = AppKit.NSMakeRect(
            end_frame.origin.x,
            end_frame.origin.y - rise,
            end_frame.size.width,
            end_frame.size.height,
        )
        panel.setAlphaValue_(0.0)
        panel.setFrame_display_(start_frame, False)
        panel.orderFrontRegardless()

        def body(ctx):
            ctx.setDuration_(_SHOW_DURATION)
            panel.animator().setAlphaValue_(1.0)
            panel.animator().setFrame_display_(end_frame, True)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(body, None)
    except Exception:
        # instant fallback — make sure nothing is left half-transparent
        try:
            panel.setFrame_display_(end_frame, False)
        except Exception:
            pass
        panel.setAlphaValue_(1.0)
        panel.orderFrontRegardless()


def _fade_out_panel(panel, on_done) -> None:
    """Fade the panel to alpha 0, then call ``on_done`` (which decides
    whether to actually orderOut — see the generation counters below).

    Degrades to calling ``on_done`` immediately.
    """
    try:

        def body(ctx):
            ctx.setDuration_(_HIDE_DURATION)
            panel.animator().setAlphaValue_(0.0)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(body, on_done)
    except Exception:
        on_done()


def _crossfade_label(label, apply_text) -> None:
    """Soften a text swap: dip the label to ~0.55 alpha, apply the new text,
    then fade back to 1.0 (~120ms total). Degrades to an instant swap."""
    try:

        def fade_out(ctx):
            ctx.setDuration_(_TEXT_FADE_HALF)
            label.animator().setAlphaValue_(0.55)

        def then_swap_and_recover():
            apply_text()
            try:

                def fade_in(ctx):
                    ctx.setDuration_(_TEXT_FADE_HALF)
                    label.animator().setAlphaValue_(1.0)

                AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
                    fade_in, None
                )
            except Exception:
                label.setAlphaValue_(1.0)

        AppKit.NSAnimationContext.runAnimationGroup_completionHandler_(
            fade_out, then_swap_and_recover
        )
    except Exception:
        apply_text()
        try:
            label.setAlphaValue_(1.0)
        except Exception:
            pass


def _body_font() -> AppKit.NSFont:
    try:
        return AppKit.NSFont.systemFontOfSize_weight_(
            16, AppKit.NSFontWeightMedium
        )
    except Exception:
        return AppKit.NSFont.systemFontOfSize_(16)


class Overlay:
    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._label: AppKit.NSTextField | None = None
        self._last_text = ""
        self._base_frame = None
        # bumped on every show/hide so a stale fade-out completion never
        # orders out a panel that show() has since brought back
        self._gen = 0

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
        panel, content = _make_glass_panel(rect, _CORNER_RADIUS)

        label = AppKit.NSTextField.wrappingLabelWithString_("")
        label.setFrame_(AppKit.NSMakeRect(16, 8, _WIDTH - 32, _HEIGHT - 16))
        label.setTextColor_(AppKit.NSColor.whiteColor())
        label.setFont_(_body_font())
        content.addSubview_(label)

        self._panel, self._label, self._base_frame = panel, label, rect

    def _show_main(self) -> None:
        self._ensure_panel()
        self._gen += 1  # cancels any in-flight fade-out's orderOut
        self._last_text = ""
        self._label.setStringValue_("…")
        try:
            self._label.setAlphaValue_(1.0)
        except Exception:
            pass
        _fade_in_panel(self._panel, self._base_frame)

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
        label = self._label
        _crossfade_label(label, lambda: label.setStringValue_(shown))

    def _hide_main(self) -> None:
        if self._panel is None:
            return
        self._gen += 1
        gen = self._gen
        panel = self._panel

        def done() -> None:
            # only orderOut if no show() arrived while we were fading
            if gen == self._gen:
                panel.orderOut_(None)

        _fade_out_panel(panel, done)


class NotchIndicator:
    """Small 'listening' pill top-center near the notch with a live mic level,
    so you can see the app is hearing you before any text streams in."""

    _W, _H = 180, 26
    _BAR_X, _BAR_MAX = 100, 68

    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._bar: AppKit.NSView | None = None
        self._base_frame = None
        self._gen = 0

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
        panel, content = _make_glass_panel(rect, self._H / 2)

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

        self._panel, self._bar, self._base_frame = panel, bar, rect

    def _show_main(self) -> None:
        self._ensure_panel()
        self._gen += 1
        self._level_main(0.0)
        # fade only — no rise; the pill hugs the top edge of the screen
        _fade_in_panel(self._panel, self._base_frame, rise=0.0)

    def _level_main(self, level: float) -> None:
        if self._bar is not None:
            width = 4 + max(0.0, min(1.0, level)) * (self._BAR_MAX - 4)
            self._bar.setFrame_(
                AppKit.NSMakeRect(self._BAR_X, self._H / 2 - 3, width, 6)
            )

    def _hide_main(self) -> None:
        if self._panel is None:
            return
        self._gen += 1
        gen = self._gen
        panel = self._panel

        def done() -> None:
            if gen == self._gen:
                panel.orderOut_(None)

        _fade_out_panel(panel, done)
