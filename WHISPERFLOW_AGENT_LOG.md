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
| VSR Research | evaluate chaplin / AV-HuBERT-class local lip reading | memo below | RUNNING |

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
