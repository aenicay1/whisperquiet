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
import objc
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


# -- frosted-glass spectrum view ---------------------------------------------

# single teal accent — liquid glass, NOT a rainbow. Encode level by HEIGHT and
# ALPHA only; never shift hue per bar.
# liquid glass: translucent cool-white, not a saturated accent
_SPECTRUM_RGB = (0.90, 0.95, 1.0)
_SPECTRUM_ATTACK = 0.30  # how fast bars rise toward a louder target (lower = smoother)
_SPECTRUM_ALPHA_REST = 0.55
_SPECTRUM_ALPHA_PEAK = 1.0
_SPECTRUM_DECAY = 0.8  # prev*0.8 floor → bars drift down gracefully, never strobe


class _SpectrumView(AppKit.NSView):
    """Thin vertical bars mirrored around a horizontal centre line, drawn in a
    single translucent teal so it reads as liquid glass on the frosted panel.

    Heights ease toward each target but fall slowly (``max(target, prev*decay)``)
    so a pause drifts down instead of strobing. Every draw is wrapped so a
    drawing failure degrades to an empty (harmless) view rather than crashing.
    """

    def initWithFrame_(self, frame):
        self = objc.super(_SpectrumView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._heights = []  # smoothed 0..1 per bar
        return self

    def setBands_(self, bands) -> None:
        try:
            target = [max(0.0, min(1.0, float(b))) for b in (bands or [])]
        except Exception:
            target = []
        if not target:
            self._heights = []
            self.setNeedsDisplay_(True)
            return
        prev = self._heights
        if len(prev) != len(target):
            prev = [0.0] * len(target)
        # spatial smoothing: blur each band with its neighbors so adjacent
        # bars flow into one another instead of spiking independently
        n = len(target)
        smoothed = [
            0.25 * target[max(0, i - 1)]
            + 0.5 * target[i]
            + 0.25 * target[min(n - 1, i + 1)]
            for i in range(n)
        ]
        # temporal easing: rise gently toward a louder target (attack), fall
        # slowly (decay floor) — no instant snaps, reads as fluid
        self._heights = [
            p + (t - p) * _SPECTRUM_ATTACK if t > p else max(t, p * _SPECTRUM_DECAY)
            for t, p in zip(smoothed, prev)
        ]
        self.setNeedsDisplay_(True)

    def isFlipped(self) -> bool:
        return False

    def drawRect_(self, rect) -> None:
        try:
            heights = list(self._heights)
            if not heights:
                return
            bounds = self.bounds()
            w = float(bounds.size.width)
            h = float(bounds.size.height)
            n = len(heights)
            cx = h / 2.0
            slot = w / n
            bar_w = max(1.0, slot * 0.42)
            r, g, b = _SPECTRUM_RGB
            # dark backing strip so light bars keep contrast on any background
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.28).set()
            AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                AppKit.NSMakeRect(0, 0, w, h), 6.0, 6.0
            ).fill()
            for i, level in enumerate(heights):
                level = max(0.0, min(1.0, level))
                # full height of the mirrored bar; clamp to at least a faint stub
                bar_h = max(2.0, level * (h - 2.0))
                alpha = _SPECTRUM_ALPHA_REST + level * (
                    _SPECTRUM_ALPHA_PEAK - _SPECTRUM_ALPHA_REST
                )
                x = i * slot + (slot - bar_w) / 2.0
                y = cx - bar_h / 2.0
                radius = min(bar_w / 2.0, 2.0)
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    r, g, b, alpha
                ).set()
                path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    AppKit.NSMakeRect(x, y, bar_w, bar_h), radius, radius
                )
                path.fill()
        except Exception:
            # never crash the run over an indicator repaint
            return


class NotchIndicator:
    """Small 'listening' pill top-center near the notch with a live mic level,
    so you can see the app is hearing you before any text streams in.

    The mic level is shown as a frosted-glass audio spectrum (``set_spectrum``);
    ``set_level`` remains as a single-bar fallback if the spectrum is never fed.
    """

    _N_BANDS = 40
    _W, _H = 240, 26
    # spectrum / fallback-bar region: right of the "listening" label
    _SPEC_X, _SPEC_W = 100, 128
    _BAR_X, _BAR_MAX = 100, 128

    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._bar: AppKit.NSView | None = None
        self._spectrum: _SpectrumView | None = None
        self._base_frame = None
        self._gen = 0

    # -- public, thread-safe ------------------------------------------------

    def show(self) -> None:
        AppHelper.callAfter(self._show_main)

    def set_level(self, level: float) -> None:
        AppHelper.callAfter(self._level_main, float(level))

    def set_spectrum(self, bands: list[float]) -> None:
        AppHelper.callAfter(self._spectrum_main, list(bands or []))

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
        bar.setHidden_(True)  # fallback only; revealed by set_level()
        content.addSubview_(bar)

        spectrum = None
        try:
            spectrum = _SpectrumView.alloc().initWithFrame_(
                AppKit.NSMakeRect(self._SPEC_X, 2, self._SPEC_W, self._H - 4)
            )
            content.addSubview_(spectrum)
        except Exception:
            spectrum = None

        self._panel, self._bar, self._spectrum = panel, bar, spectrum
        self._base_frame = rect

    def _show_main(self) -> None:
        self._ensure_panel()
        self._gen += 1
        if self._spectrum is not None:
            self._spectrum_main([0.0] * self._N_BANDS)
        else:
            self._level_main(0.0)
        # fade only — no rise; the pill hugs the top edge of the screen
        _fade_in_panel(self._panel, self._base_frame, rise=0.0)

    def _level_main(self, level: float) -> None:
        # single-bar fallback: only used when set_spectrum is never called
        if self._bar is None:
            return
        try:
            self._bar.setHidden_(False)
        except Exception:
            pass
        width = 4 + max(0.0, min(1.0, level)) * (self._BAR_MAX - 4)
        self._bar.setFrame_(
            AppKit.NSMakeRect(self._BAR_X, self._H / 2 - 3, width, 6)
        )

    def _spectrum_main(self, bands: list[float]) -> None:
        if self._spectrum is None:
            # no custom view available — fall back to the single bar using the
            # loudest band so the indicator still moves
            try:
                peak = max(bands) if bands else 0.0
            except Exception:
                peak = 0.0
            self._level_main(peak)
            return
        try:
            if self._bar is not None:
                self._bar.setHidden_(True)
            self._spectrum.setBands_(bands)
        except Exception:
            pass

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
