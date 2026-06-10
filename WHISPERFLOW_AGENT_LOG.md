# WhisperFlow Vision — Agent Build Log

Camera-control build-out for **whisperquiet** (this repo *is* the product; see
DESIGN.md for the governing decision record). Requested structure mapped onto
the existing package:

| Requested | Actual | Why |
|---|---|---|
| `/src/vision_engine/` | `whisperquiet/vision/` (perception) + `whisperquiet/control/` (mapping/action) | one package, no parallel source trees |
| `/models` | `models/` (gitignored `.task` files) | as requested |
| `/tests` | `tests/` (already existed) | as requested |
| Lip-reading (VSR) build | research memo only | backlogged in DESIGN.md with kill criterion (≥30% rel. WER gain in noise) |
| Forever QA loop | bounded fix-until-green loop | no unattended infinite loops |

## Agent roster

| Agent | Scope | Output | Status |
|---|---|---|---|
| Dependency/ML Vision | mediapipe 0.10.35 + opencv 4.13 verified on py3.14, FaceLandmarker model downloaded | `whisperquiet/vision/capture.py`, `scripts/verify_vision.py` | ✅ DONE (live FPS run blocked on camera permission) |
| OS Automation Kernel | Quartz mouse move/click/scroll; keyboard already shipped in `inject.py` | `whisperquiet/control/mouse.py`, `scripts/verify_os_control.py` | ✅ DONE — circle test PASS |
| Gesture Mapping Engine | blendshapes → events w/ hysteresis, hold times, refractory, baseline calibration | `whisperquiet/control/gestures.py`, `tests/test_gesture_engine.py` (16 tests) | ✅ DONE |
| Visual HUD | AppKit panel: status pills, 117-pt wireframe (mirrored, 30Hz-throttled), metrics, calibration bar | `whisperquiet/vision/hud.py`, `tests/test_hud_smoke.py` (9 tests) | ✅ DONE |
| Integration | capture → engine → mouse/HUD glue + menu toggle + 1.5s neutral-face calibration | `whisperquiet/vision/controller.py`, `app.py` wiring | ✅ DONE |
| QA / Self-correction | synthetic-stream e2e, blink-suppression, race, latency-budget tests | `tests/test_integration.py` | ✅ DONE — 35 passed, 2 skipped (GUI/camera-gated) |
| VSR Research | evaluate chaplin / AV-HuBERT-class local lip reading | memo below | ✅ DONE |

## Gesture map (merged: DESIGN.md decision #8 + new triggers)

| Signal (blendshapes) | Event |
|---|---|
| eyeBlinkLeft high, eyeBlinkRight low | LEFT_CLICK |
| eyeBlinkRight high, eyeBlinkLeft low | RIGHT_CLICK |
| browInnerUp high (held) | SCROLL_UP (repeating) |
| mouthPucker high (held) | SCROLL_DOWN (repeating) |
| jawOpen high ≥ 400ms | TOGGLE_DICTATION (config-gated, default off — v1 uses PTT) |

## Verification metrics

| Check | Target | Measured |
|---|---|---|
| Landmark points per frame | 468 (+ 52 blendshapes) | pending camera permission (System Settings → Privacy → Camera → Python) |
| Vision pipeline FPS | ≥ 30 | pending camera permission — rerun `scripts/verify_vision.py` |
| Per-frame gesture latency | < 50ms | ✅ well under budget on 10s synthetic stream (test_per_frame_latency_within_budget) |
| Mouse circle smoothness | smooth, returns to origin | ✅ PASS — 120 steps/2s, end pos = start pos |
| Keyboard injection | text lands in focused app | ✅ proven in live dictation use (this session) |
| Test suite | 0 failures | ✅ 35 passed, 2 skipped (gated: live camera, GUI panel) |

*(table updated as results land)*

## VSR research memo (June 2026)

Question: can any local lip-reading/AVSR stack plausibly meet the DESIGN.md
kill criterion (≥30% relative WER gain over audio-only whispered speech in
café noise, fully on-device)?

- **Chaplin** (MIT): visual-only Auto-AVSR checkpoint + local LLM cleanup.
  19.1% WER on LRS3 under ideal conditions, much worse on real webcams.
  Proves the pipeline runs on a Mac; wrong modality for us (we want fusion,
  not visual-only).
- **Auto-AVSR / AV-HuBERT**: ~250M params, RAM-trivial on M-series, real-time
  mouth-crop frontend feasible. AV-HuBERT has the best noise-robustness
  results (~50% relative at 0 dB babble) but weights are **CC-BY-NC** —
  commercial blocker. No MLX/CoreML ports exist; PyTorch-MPS needed.
- **Whisper-Flamingo / mWhisper-Flamingo**: visual cross-attention injected
  into Whisper — architecturally the natural fit for our mlx-whisper stack.
  Published: 12.6% → 5.6–7.0% WER at 0 dB babble (44–50% relative). Visual
  encoder is AV-HuBERT (license caveat applies).
- **AISHELL6-Whisper** (2025): first whispered-speech AVSR baseline
  (Mandarin, 4.13% CER) — best evidence that whispered-audio+lips fusion
  specifically works.

**Verdict:** kill criterion is plausibly met only by a Whisper-Flamingo-style
fusion, and published gains are at 0 dB SNR over *normal* speech — café noise
is 5–15 dB where visual gains shrink, and whispering adds domain shift.
Confidence ≥30% holds in our condition: **~35–45%**. Lip reading stays
backlogged. Next step when revisited: benchmark Whisper-Flamingo (PyTorch/MPS,
non-commercial eval) against mlx-whisper on recorded whispered-café samples;
shipping would additionally require an MLX port + license-clean visual
encoder.

Sources: github.com/amanvirparhar/chaplin · github.com/mpc001/auto_avsr ·
github.com/roudimit/whisper-flamingo · arxiv.org/abs/2406.10082 ·
arxiv.org/html/2502.01547v1 · arxiv.org/html/2509.23833v1

## Deep-research synthesis: whispered-speech AVSR (salvaged 2026-06-10)

Run died mid-verification at session cap; 123 extracted claims + partial
adversarial verification salvaged from the journal. Convergent findings:

1. **SNR reality kills the fusion case.** AV gains collapse in the 5–15 dB
   café band: ~6% relative at 10 dB (TorchAudio AV-ASR), gains concentrate
   at ≤0 dB and overlapping-speech interference. Noise-augmented *audio*
   training recovers most robustness without any camera.
2. **AISHELL6's own numbers undercut the lip-reading bet:** adding the visual
   stream to whispered speech improved CER only 4.21% → 4.13% (~2% relative)
   — in their clean studio, with frontal 720p video. The English transfer
   result (wTIMIT, −1.85 abs WER) was **audio-only** (wTIMIT has no video).
3. **The cheap win is audio-side:** stock Whisper ≈18.8% WER on whispered
   English; fine-tuning on ~20h whispered data roughly halves it; a tiny
   ConMamba hit 1.19% WER on wTIMIT. Whispered accuracy is fixable without
   the camera.
4. **Personalization is real and cheap:** 1–5 min of speaker data halves
   speaker-dependent VSR error (GRID); LoRA ≈1% params/speaker; quantized
   personalized Whisper runs on edge. → The flywheel pivots to AUDIO:
   harvest the user's own whispered audio + corrected transcripts from
   daily use, LoRA-fine-tune Whisper on-device (MLX). License-clean by
   construction, fully private.
5. **Licensing remains fatal for shipping VSR:** every strong visual
   encoder rests on CC-BY-NC weights (AV-HuBERT) or research-only corpora
   (LRS2/3, VoxCeleb2). On-device feasibility is proven (34.9M-param AV
   model, RTF 0.87 on laptop CPU) — gains and licenses are the blockers,
   not compute.

**Verdict: kill criterion (≥30% rel. gain at 5–15 dB) NOT met. NO-GO on
lip-reading fusion.** Camera = control; voice = text. New backlog item:
personal whispered-audio LoRA flywheel. Cheapest next experiment: record
~30–60 min of own whispered dictation w/ corrected transcripts, LoRA-tune
whisper via MLX, measure WER vs stock.

## MVP build-out (2026-06-10, agent team round 2)

| Agent | Delivered | Tests |
|---|---|---|
| Head cursor + drag | `control/head_cursor.py` (One-Euro + deadzone + gain + precision), `mouse.move_by/left_down/left_up` with drag-aware moves, jaw machine → DRAG_START/DRAG_END, `engine.winking` | 7 new + jaw suite rewritten |
| HUD visualization | per-gesture live meters w/ personal threshold ticks, event toasts (LEFT CLICK / DRAG / SCROLL ↑…), [CURSOR] status | 10 new headless |
| Overlay polish | frosted-glass panels (NSVisualEffectView + fallbacks), fading text swaps, animated show/hide | import + suite |
| Integrator | controller/app wiring (cursor toggle, drag mapping, meters feed, flash), menu + trigger-cursor, LICENSE, README, pyproject vision deps (rebuilt venv was missing mediapipe/opencv — camera mode would not start) | full suite 55 passed, 3 skipped |

Live smoke: app stable through camera-mode on → 15s capture → off; no log errors.

**MVP remaining:** the dogfood week against DESIGN.md success bar. Everything
else (Swift port, voice onboarding/LoRA, gaze) is post-MVP.

## UX round (2026-06-10 evening, 3 self-correcting agents + integrator)

Shipped: double-wink double-click (clickState=2), cheek-puff pause/resume
(only gesture live while paused), nod=Return / shake=Escape (oscillation
detector, slow pans inert), scroll momentum (0.15s→0.05s over 2s),
vocabulary biasing (config.vocabulary → whisper initial_prompt), session
stats JSONL for the dogfood success bar. Suite 76 passed, 3 skipped.
Live smoke: relaunch + camera on, frames flowing.

Backlog (ranked next): audio tick on gesture fire; jaw-shift app switcher;
login item + onboarding checklist; per-app injection profiles; Swift port.
