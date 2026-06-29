# whisperquiet — feature & improvement backlog

Running list of future work. Newest user requests at the top of each section.
Not in priority order within a section unless noted.

## Reliability
- ~~Dictation worker hung on a wedged mic, silently bricking all later PTT~~
  FIXED 2026-06-19 — MicRecorder.stop() is now time-bounded (2s watchdog +
  abandon) so the worker can't hang on PortAudio close; a busy worker now shows
  "finishing previous dictation…" instead of silently dropping presses; stream
  callbacks are gated by accepting+generation so an abandoned stream can't
  pollute a later take.
- ~~Wedged mic OPEN froze the run loop and bricked the hotkey~~ FIXED
  2026-06-26 — observed in production: an EarPods hot-swap wedged CoreAudio
  (AUHAL -10851); recorder.start() blocked on the open, and because it ran on
  the PTT event-tap (main run loop) it froze the keyboard tap, so macOS kept
  disabling the tap and the watchdog kept re-arming it (the endless
  "PTT tap was disabled — re-enabled" loop = "hotkey stopped working"). FIX:
  recorder.start() now runs on the dictation WORKER thread (app._stream_loop),
  never the run-loop tap handler, so a blocking open stalls only that one
  dictation and the hotkey stays live. NB: an earlier attempt that bounded
  start() with a watchdog/abandon was scrapped after adversarial review —
  abandoning an in-flight PortAudio open is unsafe (a slow-but-successful open
  commits a stream nobody closes; the rescan's process-global sd._terminate()
  can tear down a concurrent open → use-after-free). The single-threaded
  worker-side open avoids all of that.
- **KNOWN RESIDUAL: a truly hung mic OPEN soft-bricks dictation until restart.**
  If sd.InputStream(...).start() blocks forever on a wedged device the worker
  thread hangs (Python can't kill it), so later presses show "finishing previous
  dictation…" and dictation stops until relaunch — BUT the hotkey/UI stay
  responsive (no freeze). Same class as the hung-decode residual below; the real
  fix is an out-of-process audio engine we can kill.
- **Native-rate mic open** SHIPPED 2026-06-28 (e084d9c) — open at the device's
  native sample rate instead of forcing 16 kHz, since the rate renegotiation is
  what trips the AUHAL -10851. Cuts wedge FREQUENCY; does not guarantee recovery.
- ~~Whole-app auto-relaunch backstop (kill+reopen the .app on a detected wedge)~~
  **REJECTED 2026-06-29 after adversarial review** (24 agents, 18 confirmed
  findings, 3 high). Four fatal problems: (1) it barely fires — recovery was
  gated on _recording still being set at the 8s mark, but the 4s "mic stuck"
  notice makes the user release first, clearing _recording and disarming it, so
  it no-ops in the common tap-and-release case; (2) cooldown defeated → relaunch
  storm if CONFIG_DIR is unwritable (the persisted-timestamp brake silently
  fails); (3) app vanishes entirely if `open` fails after os._exit (strictly
  worse than the soft-brick); (4) false-positive force-kill of a slow-but-
  successful open at the 8s boundary. A blunt whole-app relaunch tied to PTT
  timing is the wrong tool. **The only safe auto-recovery is the out-of-process
  audio engine** (kill+respawn just the audio child; supervisor in the always-
  alive parent; heartbeat detection decoupled from PTT/_recording). That is the
  next build — interim today is honest visibility ("mic stuck") + manual restart.
- **KNOWN RESIDUAL: a truly hung MLX decode still needs a restart.** If a
  partial/finalize decode ever wedges the GPU, it holds `_tx_lock` forever, so
  the worker stays alive and new dictations block on the lock — no in-process
  recovery is possible (you cannot kill a Python thread, and a watchdog "new
  session" would just block on the same lock). Never observed in practice; the
  real fix is running decode out-of-process (subprocess we can kill) or a
  bounded `_tx_lock.acquire(timeout=...)` fallback. Lower priority than it
  sounds — decodes are bounded in normal operation.

## Dictation quality
- **Phase 0 shipped 2026-06-18** — accuracy-gap groundwork (build the levers +
  the ruler, enable after data):
  - WER baseline is now tracked in `results/baseline.json` (was only in the
    fba0710 commit message: 16.3% normalized WER on real recordings, ~11pts
    over the 5% bar). Regenerate reproducibly with `bench_wer.py --json
    results/baseline.json --condition quiet|cafe|whisper`. **TODO (needs user):**
    record fresh quiet + café + whispered full reads of the ref sheet.
  - Speech-presence gate `whisperquiet/vad.py` wired as a default-OFF commit
    gate (`config.vad_gate_enabled`). Built-in detector is conservative — it
    rejects only silence and steady tonal noise, never low-energy whisper.
    **Upgrade path:** swap in a Silero VAD ONNX detector (onnxruntime, no torch)
    behind the same `is_speech` seam, then A/B on breath/silence + whisper
    fixtures before turning it on by default.
  - Commit-latency instrumentation: `stats.jsonl` now logs `commit_latency_ms`
    (release→inject) + `transcribe_ms`; `report.py` shows p50/p95 vs the <1s
    DESIGN target. **TODO:** dogfood to populate real numbers.
  - Denoiser A/B harness `scripts/bench_denoise.py` + `whisperquiet/denoise.py`
    (optional `[denoise]` extra, noisereduce, no torch). Offline only — denoise
    is NOT in the live path until this harness shows a per-environment WER win.
    **TODO (needs user):** record noisy/café reads, run the A/B, record verdict.
- ~~**Parakeet TDT vs whisper-turbo backend decision**~~ DECIDED 2026-06-19:
  **KEEP WHISPER TURBO.** Evaluated parakeet-tdt-0.6b-v3 against turbo on the
  real quiet + whisper takes (same scorer, no vocab on either side):
  Parakeet 7.1% / 11.2% WER vs raw turbo 6.1% / 9.2% — Parakeet LOSES on
  accuracy in both conditions, and shipping turbo+vocab (2.0% / 7.1%) beats it
  decisively. Parakeet's only win is ~2x latency (~0.9s vs ~2.1s/take steady
  state), which doesn't matter when turbo is already fast enough and accuracy is
  the gap. It also has NO vocab biasing, so it mangled the domain terms turbo
  gets right (Circleback→"circle back", Springdale→lowercase, dropped "EBITDA").
  parakeet-mlx is left wired as the opt-in `[parakeet]` backend for future
  re-eval. If revisited: (1) parakeet.py currently writes a temp WAV + calls
  model.transcribe(path), which needs ffmpeg — use the array path instead
  (`get_logmel(mx.array(audio), model.preprocessor_config)` -> `model.generate`,
  no ffmpeg); (2) it only earns the default if NeMo-style word boosting lands in
  the MLX port to close the vocab gap.
- ~~[HIGH] Long-form dictation garbling~~ DONE 2026-06-14 — VAD chunking (transcribe_long).
- (orig note, user req 2026-06-12) —
  a 45s continuous "yap" lost sentences 2-6 entirely; one-shot transcription
  of long buffers fails, worsened by condition_on_previous_text=False (set to
  kill hallucination loops, but it also breaks long-form window coherence).
  Core use case = long context-rich brain-dumps to LLMs, so this is a
  priority. FIX: VAD/silence-based chunking — split the buffer at natural
  pauses into short segments, transcribe each (Whisper's sweet spot), stitch.
  Each chunk short enough to avoid the drop; also enables a cleaner streaming
  preview. Per-chunk hallucination guard stays.
- **Background noise reduction / denoising** (user req 2026-06-12) — clean
  the mic signal before Whisper sees it: an on-device denoiser (RNNoise /
  DeepFilterNet-class) or spectral noise-gate, ideally calibrated to the
  user's room. Helps the hard cases (cafés, fans, AC). On-device only.
- ~~Normalized WER scoring (strict vs recognition-only)~~ DONE 2026-06-12 — playground shows both.
- ~~Time formatting (7.45pm -> 7:45 PM)~~ DONE 2026-06-12 — cleanup format_times.
- ~~Live preview ≠ committed text~~ DONE 2026-06-14 — incremental transcription makes the preview the source of truth (each segment locked once).
- **(was) Live preview ≠ committed text** — the streaming
  text in the floating modal often differs from what finally lands in the
  text box, which is disorienting. Make the preview a truer reflection of
  the final: e.g. only show stabilized words, apply the cleanup layer to the
  preview, or hold the preview until confidence settles. Reduce the "I saw X,
  it pasted Y" whiplash.
- ~~LLM rescoring (fix implausible errors)~~ SHIPPED 2026-06-14, off by default (rescore_enabled); needs mlx_lm + real-latency cancellation before default-on.
- **Level-2 polish A/B 2026-06-19 (scripts/bench_polish.py over 40 real dictations,
  Qwen2.5-1.5B-4bit): KEEP OFF.** Latency OK (p50 490ms, p95 1.5s, applied 90%,
  changed 35%), but the model OVER-EDITS: on ~1/3 of its changes it rephrased or
  dropped specifics it was told to preserve — guessed "Clay" -> "a typo", dropped
  "Instantly" and "buy and block", deleted "including calendar invites". Unsafe
  for context-rich dictation. Insight: punctuation/sentence-splitting edits were
  safe+good; WORD-level edits were the danger. Paths to revisit (ranked): (1)
  harden prompt + drop _MAX_LENGTH_DELTA ~0.35->0.15 to auto-kill big rewrites,
  re-run A/B; (2) if small-model restraint stays poor, scope to punctuation/caps
  only (no word changes); (3) try a more capable model and measure the latency
  cost. Also: even meaning-preserving, it adds ~0.5s p50 to every commit — only
  worth enabling where the polish clearly beats that responsiveness cost.
- LoRA fine-tune on the user's own voice/whisper (the structural WER win;
  corpus already accumulating in audio/ + transcripts.jsonl).
- Auto-vocabulary harvesting — propose vocab entries from observed edits
  (consent-gated) instead of manual `wq-vocab add`.
- Smarter decoding spikes — beam width, style-priming initial_prompt,
  cross-utterance context.

## Hardware / input
- AirPods / wired-headset mic A/B for whispered WER (TESTING NOW 2026-06-12).
- Mic-distance guidance in onboarding (close mic >> any model tweak).

## Menu bar / app shell
- Proper status-bar item + global hotkey (Swift port) so the notch-overflow
  hidden-icon problem disappears. Interim: trigger files + wq-* aliases.

## Camera (frozen surface — tuning only, per Pivot #2)
- Experimental head cursor / jaw drag / nod-shake remain config-off for the
  RSI/a11y audience; no new gestures.

## Platform / launch
- **Self-contained freeze SHIPPED 2026-06-29** — `dist/WhisperQuiet.app` is now a
  real distributable bundle (PyInstaller, `scripts/build_app.sh` +
  `packaging/whisperquiet.spec`), not the dev PYTHONPATH wrapper (make_app.sh).
  Runs detached from the repo/.venv. 388 MB, arm64-only, dictation-only (camera
  stack excluded → its menu items + trigger files are gated off when mediapipe/cv2
  are absent). Smoke-validated: launches once, warms the mlx model + runs a Metal
  decode, installs the PTT tap, no fork-bomb. Key freeze fixes baked in:
  `multiprocessing.freeze_support()` in the entry (spawn would otherwise re-run
  the app → fork bomb); `pyobjc-framework-ApplicationServices` is a required dep
  (AXIsProcessTrusted; not pulled transitively); `_sounddevice_data` (PortAudio)
  + cffi force-collected (lazy import in an except branch the graph can't see).
  py2app was tried first and abandoned — mlx is a PEP-420 namespace package with
  a compiled ext + sibling Metal lib that py2app's legacy imp finder can't handle.
- **NEXT GATE — public download needs Developer ID + notarization** (blocked on a
  $99/yr Apple Developer account). The bundle is currently ad-hoc signed: runs
  locally, but Gatekeeper warns/blocks downloaders ("Apple cannot check it for
  malicious software"). build_app.sh already has the inside-out hardened-runtime
  signing path (WQ_SIGN_ID=...) + packaging/entitlements.plist (allow-jit /
  allow-unsigned-executable-memory / disable-library-validation, no sandbox).
  Once the account exists: sign with the Developer ID, `xcrun notarytool submit`,
  `stapler staple`, then host the DMG + a download site.
- **First-run UX — largely SHIPPED 2026-06-29.** (1) Hotkey is now live BEFORE the
  model load (`ptt.start()` moved ahead of `warm_up`); a press while `_model_ready`
  is clear shows "loading model…" instead of dead keys (gated in `_on_ptt_press`,
  regression-tested in tests/test_model_loading_gate.py). (2) The first-run
  download is now visible — a persistent notch + a menu line ("downloading model
  (first run, ~1.6 GB)…" vs "loading model…", chosen by `_model_is_cached`) so it
  never looks frozen. (3) App icon shipped (packaging/whisperquiet.icns, the 🤫
  mark on an indigo squircle). (4) First-run permission onboarding: a one-time
  modal that lists missing TCC grants and — crucially — tells the user to RELAUNCH
  (macOS only applies Accessibility/Input-Monitoring at launch); self-correcting,
  never shows once all granted.
  REMAINING (nice-to-have): a real download PROGRESS % / bar (currently just an
  indeterminate "downloading…"), and the icon is emoji-derived — fine for v1, a
  custom mark later.
- Swift/SwiftUI port (clean TCC identity, status item, lower idle cost).
- Login-item + first-run onboarding checklist.
- Lip-reading AV fusion — backlogged behind kill criterion (≥30% rel. WER
  gain in café noise, on-device); NO-GO at current research.
