# whisperquiet — feature & improvement backlog

Running list of future work. Newest user requests at the top of each section.
Not in priority order within a section unless noted.

## Dictation quality
- **[HIGH] Long-form dictation drops/garbles content** (user req 2026-06-12) —
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
- **Live preview ≠ committed text** (user req 2026-06-12) — the streaming
  text in the floating modal often differs from what finally lands in the
  text box, which is disorienting. Make the preview a truer reflection of
  the final: e.g. only show stabilized words, apply the cleanup layer to the
  preview, or hold the preview until confidence settles. Reduce the "I saw X,
  it pasted Y" whiplash.
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
