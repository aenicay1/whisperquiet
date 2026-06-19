# whisperquiet — feature & improvement backlog

Running list of future work. Newest user requests at the top of each section.
Not in priority order within a section unless noted.

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
- Swift/SwiftUI port (clean TCC identity, status item, lower idle cost).
- Login-item + first-run onboarding checklist.
- Lip-reading AV fusion — backlogged behind kill criterion (≥30% rel. WER
  gain in café noise, on-device); NO-GO at current research.
