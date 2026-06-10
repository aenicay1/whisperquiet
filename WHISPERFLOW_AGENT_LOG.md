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
