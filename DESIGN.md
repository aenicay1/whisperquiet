# whisperquiet — Design Decision Record

**One-liner:** A macOS menu-bar app that turns whisper-quiet speech + your webcam into a complete input layer: dictation with your voice, cursor and clicks with your head and face.

**Thesis:** Camera + voice is the future of interfacing. The viable wedge today is *not* lip reading (research-grade) but head-pose cursor control + facial-gesture clicks + whisper dictation — every piece proven, the combination novel.

---

## Decisions (resolved 2026-06-09)

| # | Branch | Decision | Why |
|---|--------|----------|-----|
| 1 | Input mode | **Whisper + mic** (not silent mouthing) | Visual-only VSR is ~25–40% WER — unusable. Faint whisper + mic is near-normal accuracy and still solves "don't disturb the room." |
| 2 | Platform | **macOS menu-bar app** | Built-in camera/mic, Apple Silicon runs models locally, matches daily dogfood environment. |
| 3 | Inference | **Fully on-device** | Face video is privacy-radioactive. "Your face never leaves your Mac" is the pitch. Zero inference cost, works offline. |
| 4 | Camera's job in v1 | **Control (head + gestures), NOT lip reading** | Key pivot: webcam gaze-pointing is weak (2–4cm error) but head-pose + blendshape gestures is shipping-quality (Project Gameface proves it). Lip-reading fusion → research backlog. |
| 5 | Target user | **Self-dogfood → RSI/accessibility beachhead** | Talon-style users forgive rough edges, give expert ergonomic feedback, evangelize hard. General productivity market later. |
| 6 | Activation | **Hold-key PTT for dictation; hotkey/menu toggle for head control** with visible state indicator | Avoids the Midas-touch false-positive swamp on day one. Fully hands-free activation gestures are v1.1. |
| 7 | Stack | **Python prototype → Swift/SwiftUI port** | Python: MediaPipe Face Landmarker (52 blendshapes) + MLX Whisper + Quartz CGEvents + rumps. Tune interaction in days. Port the shell once the design stops moving. |
| 8 | Click gestures | **Left/right wink = left/right click; open-mouth-hold = drag; pucker/smile = scroll mode** | Natural blinks fire 15–20×/min — blink-to-click guarantees false clicks. Winks are deliberate and blendshape-distinguishable. All thresholds user-tunable. |
| 9 | Cursor mapping | **Relative displacement + tunable gain, deadzone, One-Euro filter, precision mode** (squint-hold or auto-slowdown near targets) | No calibration step ever; raw webcam head precision is ~50–100px so smoothing + precision mode are core product, not polish. |
| 10 | Dictation UX | **Streaming into a floating overlay; commit final text to target app on PTT release** | Streaming feel without cross-app correction brittleness — hypothesis revisions happen inside our overlay, never via synthetic backspace in someone else's app. |
| 11 | License | **Open core (MIT/Apache)** — paid Pro tier later | Verifiable privacy claim, a11y community goodwill, contributors tuning gestures across diverse faces. |

## Backlog (explicitly deferred)
- **Lip-reading AV fusion** — revisit with kill criterion: camera must cut whispered-speech WER ≥30% *relative* in café-level noise vs audio-only, on-device, or it stays shelved.
- Gaze-warp coarse pointing (teleport cursor to gaze region, refine with head).
- Voice editing commands ("scratch that", "new paragraph") — needs command/dictation disambiguation.
- Fully hands-free activation (open-mouth-to-talk, double-brow toggle).
- Windows port; code-dictation grammars.

## Known risks
1. **Whispered speech WER on stock Whisper** — whisper-mode is out-of-distribution (no voicing). Measure in week 1; fine-tune on whispered data if needed.
2. **Gesture false positives across faces** — thresholds must be per-user tunable from day one.
3. **Battery/thermals** — continuous camera + landmarker + ASR. Budget and measure early; camera pipeline must fully release when control is toggled off.
4. **Permission onboarding** — camera + microphone + Accessibility is a scary triple-prompt. Onboarding flow needs real design care.
5. **Python prototype immortality** — set an explicit "interaction design frozen" trigger for the Swift port.

## Proposed v1 success bar (dogfood exit criteria)
- One full workday: < 5 trackpad touches/hour, < 1 false click/hour.
- Whispered dictation in a quiet room: ≥ 95% word accuracy, overlay text starts appearing < 500ms after speech starts, commit < 1s after release.

## Rough sequence
1. **Week 1–2:** Python skeleton — menu bar, PTT, MLX Whisper streaming → overlay → commit-on-release. Measure whispered-speech WER (risk #1).
2. **Week 3–4:** Head cursor (relative + One-Euro + deadzone) and wink/mouth gestures with a threshold-tuning panel.
3. **Then:** Daily dogfood against the success bar; tune until it survives a full workday.
4. **Then:** Swift/SwiftUI port; RSI/a11y community beta (Talon forums, r/RSI, a11y X); open-source launch.
