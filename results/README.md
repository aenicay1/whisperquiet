# results/

Tracked measurement artifacts so accuracy/latency numbers live in the repo,
not in commit-message prose.

## baseline.json — dictation WER

The whispered/real-world WER gap is the project's #1 risk (DESIGN.md risk #1).
This file makes the current number explicit and tracked against the **≤5% WER
(≥95% accuracy)** success bar.

### Regenerating the baseline (reproducible)

1. Record full reads of the reference sheet through the app, so they land in the
   on-device audio store (`~/Library/Application Support/whisperquiet/audio`).
   Read **all** lines of [`scripts/bench-sentences.txt`](../scripts/bench-sentences.txt)
   in order, one take per condition:
   - a **quiet room**, normal volume
   - a **noisy** environment (café / fan / AC) — feeds `bench_denoise.py`
   - **whispered**, the real dictation case
2. Score and record the best variant:

   ```sh
   .venv/bin/python scripts/bench_wer.py scripts/bench-sentences.txt \
       --takes 2 --json results/baseline.json
   ```

   `--json` appends a reproducible measurement (date, model, prompt, WER) to
   `baseline.json`. WER is **normalized** (lowercased, punctuation stripped),
   scored against the full concatenation of the reference lines.

### Why the spread

Clean read-aloud speech scores well (~4–5% WER per-clip); "real recordings"
full-takes sit at ~16%. That spread — driven by mic distance, noise, and
whisper-mode out-of-distribution audio — is the gap Phase 0 attacks (denoiser
front-end, speech gate, mic-distance onboarding), not the model itself.
