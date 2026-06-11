# PLAN: Dictation cleanup layer (Wispr-Flow-style) — next session

Problem: fillers ("um", "uh", "you know"), self-corrections ("Tuesday — no
wait, Wednesday"), and word repeats leak into committed text. Fix = a
post-processing pipeline between final transcript and injection, applied to
streaming partials too so the preview matches what will land.

## Stages
1. **Rules engine** (`whisperquiet/cleanup.py`, pure + tested):
   filler removal (configurable list; default conservative — "um/uh/erm"
   always, "like/you know" opt-in), word-repeat collapse ("the the"),
   spoken commands ("new line", "new paragraph"), punctuation/spacing
   normalization. Zero latency cost.
2. **Self-correction resolution**: cue-grammar rules first ("no wait",
   "scratch that", "I mean", "actually make that X"). Fuzzy cases → stage 3.
3. **LLM polish (config-gated, off by default)**: small on-device model via
   mlx-lm (e.g. Qwen3-1.7B/4B) rewriting the final transcript only.
   Privacy-identical (on-device). Hard latency budget: <400ms p95 on M5 or
   it ships disabled.
4. **Playground**: dictation drill gains cleanup on/off toggle + a
   "disfluent sentences" set (reference excludes the fillers the prompt
   tells you to include) → measures cleanup precision/recall, not just WER.

## Agent split (tonight)
- A: cleanup.py rules + golden-pair corpus tests
- B: mlx-lm integration + latency benchmark + prompt design
- C: playground drill additions
- Integrator: pipeline wiring (partials + final), config (`cleanup_mode:
  off|rules|llm`), settings-tab knobs, commit gating.

## Decisions to make at session start
- Default filler list aggressiveness; does "like" make the cut?
- LLM model pick + whether partials get rules-only (likely yes).
- Keep raw transcript in stats for before/after comparison? (feeds flywheel)
