"""Notch status indicator — the app's single status surface.

A small frosted-glass pill top-center near the notch, shown as a non-activating
NSPanel so the target app keeps keyboard focus. It is the ONLY status surface
(there is no bottom modal): listening + a live audio spectrum on one side, and
every other state — working/finalizing, a flag, a stuck mic, "no speech" — shown
with a colored dot + short label on the left and a glyph on the right, so nothing
ever fails silently. All AppKit work is marshalled to the
main thread; public methods are safe to call from worker threads.

Visuals: a frosted-glass NSVisualEffectView (HUD material) with soft fade
transitions. Every animation path degrades to an instant show/hide/swap if the
underlying AppKit API is unavailable — never crash over polish.
"""

from __future__ import annotations

import AppKit
import objc
import traceback
from PyObjCTools import AppHelper

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
    try:
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
    except Exception:
        pass

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
    """The app's single status surface: a small pill top-center near the notch.

    Left zone: a colored dot + short label. Right zone: EITHER the live audio
    spectrum (while listening) OR a status glyph (working/flagged,
    a warning/loading label). Because there is no bottom modal, every state — a stuck
    mic, a flag, "no speech" — shows here, never silently.

    Built-in modes via the mode methods; ``notify(glyph, label)`` shows an
    arbitrary one-off status. ``set_spectrum``/``set_level`` feed the listening
    spectrum. All public methods marshal to the main thread.
    """

    _N_BANDS = 40
    _W, _H = 260, 26
    _DOT_X = 12
    _LABEL_X, _LABEL_W = 30, 176
    _GLYPH_X, _GLYPH_W = 210, 36
    _SPEC_X, _SPEC_W = 100, 128
    _BAR_X, _BAR_MAX = 100, 128

    # mode -> (dot color, left label, right glyph or None=show spectrum)
    _MODES = {
        "listening": ("red", "listening", None),
        "working": ("gray", "working…", "..."),
        "flagged": ("red", "flagged", "!"),
    }
    _DOT_COLORS = {
        "red": "systemRedColor",
        "orange": "systemOrangeColor",
        "gray": "systemGrayColor",
    }

    def __init__(self) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._dot = None
        self._label = None
        self._glyph = None
        self._bar = None
        self._spectrum: _SpectrumView | None = None
        self._base_frame = None
        self._gen = 0
        self._mode: str | None = None

    # -- public, thread-safe ------------------------------------------------

    def show(self) -> None:  # back-compat alias: default to listening mode
        AppHelper.callAfter(self._mode_main, "listening")

    def listening(self) -> None:
        AppHelper.callAfter(self._mode_main, "listening")

    def working(self) -> None:
        AppHelper.callAfter(self._mode_main, "working")

    def flagged(self) -> None:
        AppHelper.callAfter(self._mode_main, "flagged")

    def notify(self, glyph: str, label: str) -> None:
        AppHelper.callAfter(self._notify_main, glyph, label)

    def set_level(self, level: float) -> None:
        AppHelper.callAfter(self._level_main, float(level))

    def set_spectrum(self, bands: list[float]) -> None:
        AppHelper.callAfter(self._spectrum_main, list(bands or []))

    def hide(self) -> None:
        AppHelper.callAfter(self._hide_main)

    # -- main thread only ---------------------------------------------------

    def _ns_color(self, name: str):
        getter = getattr(
            AppKit.NSColor, self._DOT_COLORS.get(name, "systemRedColor")
        )
        return getter()

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
        dot.setFrame_(AppKit.NSMakeRect(self._DOT_X, 4, 14, 18))
        dot.setTextColor_(AppKit.NSColor.systemRedColor())
        dot.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        content.addSubview_(dot)

        label = AppKit.NSTextField.labelWithString_("listening")
        label.setFrame_(AppKit.NSMakeRect(self._LABEL_X, 4, self._LABEL_W, 18))
        label.setTextColor_(AppKit.NSColor.whiteColor())
        label.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        content.addSubview_(label)

        glyph = AppKit.NSTextField.labelWithString_("")
        glyph.setFrame_(AppKit.NSMakeRect(self._GLYPH_X, 3, self._GLYPH_W, 20))
        glyph.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        glyph.setHidden_(True)
        content.addSubview_(glyph)

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

        self._panel, self._dot, self._label = panel, dot, label
        self._glyph, self._bar, self._spectrum = glyph, bar, spectrum
        self._base_frame = rect

    def _apply(self, color: str, text: str, glyph: str | None, dim_dot: bool) -> None:
        try:
            self._dot.setHidden_(bool(dim_dot))
            if not dim_dot:
                self._dot.setTextColor_(self._ns_color(color))
            self._label.setStringValue_(text)
            if glyph is None:
                self._glyph.setHidden_(True)
                if self._spectrum is not None:
                    self._spectrum.setHidden_(False)
            else:
                self._glyph.setStringValue_(glyph)
                self._glyph.setHidden_(False)
                if self._spectrum is not None:
                    self._spectrum.setHidden_(True)
                if self._bar is not None:
                    self._bar.setHidden_(True)
        except Exception:
            pass

    def _mode_main(self, mode: str) -> None:
        try:
            self._ensure_panel()
            color, text, glyph = self._MODES.get(mode, self._MODES["listening"])
            self._mode = mode
            self._apply(color, text, glyph, dim_dot=(mode == "working"))
            self._show_panel()
            self._maybe_spin(mode)
        except Exception:
            print("notch indicator: failed to show mode", flush=True)
            traceback.print_exc()

    def _notify_main(self, glyph: str, label: str) -> None:
        try:
            self._ensure_panel()
            self._mode = "notify"
            self._apply("red", label, glyph, dim_dot=True)
            self._show_panel()
        except Exception:
            print("notch indicator: failed to show notification", flush=True)
            traceback.print_exc()

    def _show_panel(self) -> None:
        self._gen += 1  # cancels any in-flight fade-out's orderOut + stale spin
        # fade only — no rise; the pill hugs the top edge of the screen
        _fade_in_panel(self._panel, self._base_frame, rise=0.0)

    def _maybe_spin(self, mode: str) -> None:
        # Pulse the working glyph so the indicator does not look frozen.
        # Guarded by the generation counter + current mode, so it stops the
        # instant the mode changes or the panel hides.
        if mode != "working":
            return
        gen = self._gen

        def tick(done=False):
            if gen != self._gen or self._mode != "working":
                return
            try:
                self._glyph.setStringValue_(".." if done else "...")
            except Exception:
                return
            AppHelper.callLater(0.6, tick, not done)

        try:
            AppHelper.callLater(0.6, tick, True)
        except Exception:
            pass  # static hourglass is fine

    def _level_main(self, level: float) -> None:
        # single-bar fallback: only used when set_spectrum is never called
        if self._bar is None or self._mode not in (None, "listening"):
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
        self._mode = None

        def done() -> None:
            if gen == self._gen:
                panel.orderOut_(None)

        _fade_out_panel(panel, done)
