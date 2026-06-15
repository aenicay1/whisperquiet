"""Optional on-device LLM rescoring: stage 3 of docs/PLAN-dictation-cleanup.md.

Rules-based cleanup (cleanup.py) fixes mechanical errors on exact cues. It can't
fix a *recognition* error where Whisper heard a plausible-sounding wrong word:
"purchase agreements to the Warriors" should be "...to the lawyers", "camera for
missions and set up" should be "camera permissions and setup", "letter from Zed"
should be "letter of intent". Those need a language model that knows which
phrasing is semantically plausible.

This stage runs a small instruction-tuned LLM (via mlx_lm) to repair only those
homophone-ish / implausible-phrase errors, changing nothing else. It is OFF by
default and opt-in: the disabled path returns the input untouched and never even
imports mlx_lm, so the dependency is optional.

SAFETY is the whole design. A rescorer that paraphrases, adds content, or
refuses is worse than the original transcription, because the output goes
straight into the user's document. So rescore() never raises (any failure —
missing dep, model load error, timeout — returns the ORIGINAL text), enforces a
wall-clock latency budget, and runs a battery of sanity checks on the model
output (non-empty, length within bounds, no refusal/preamble patterns); on any
failure it DISCARDS the model output and returns the original. The single seam
that actually talks to the model is the module-level _generate hook, so tests
monkeypatch it and never download a real model.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

# Length guard: if the rescored text differs from the input length by more than
# this fraction, treat it as a runaway rewrite and discard it. A genuine
# homophone fix barely moves the length; a paraphrase or hallucinated addition
# moves it a lot.
_MAX_LENGTH_DELTA = 0.60

# Refusal / preamble / meta patterns. A correction is *only* the corrected text,
# so any of these means the model editorialized instead of correcting; discard.
_REFUSAL_PATTERNS = [
    # Leading word-tokens that signal a preamble/refusal. The trailing \b stops
    # a legit correction that merely *starts with* one of these words as real
    # content ("Surely the deal closes", "Certainly Industries signed") from
    # being misread as a refusal — without \b, "sure" matched "Surely".
    re.compile(r"^\s*(?:sure|certainly|of course|okay|ok|here(?:'s| is)|"
               r"the corrected|corrected text|i (?:can|cannot|can't|'m|am)|"
               r"as an ai|i'm sorry)\b", re.IGNORECASE),
    # Leading punctuation-terminated meta labels: the literal trailing char is
    # the boundary, so no \b (which would have broken these).
    re.compile(r"^\s*(?:sorry,|note:|output:|result:)", re.IGNORECASE),
    re.compile(r"\b(?:as an ai|language model|i (?:cannot|can't) )", re.IGNORECASE),
]

_SYSTEM_PROMPT = (
    "You correct speech-to-text recognition errors in dictated text. "
    "A speech recognizer sometimes hears the wrong word that sounds similar to "
    "the intended one, producing an implausible phrase. Fix ONLY those obvious "
    "recognition errors: replace a wrong homophone-like word or an implausible "
    "phrase with the word the speaker clearly meant. Do NOT rephrase, do NOT "
    "change style, do NOT add or remove content, do NOT fix grammar or "
    "punctuation, and leave any text that is already correct exactly as is. "
    "Output ONLY the corrected text with no preamble, quotes, or explanation."
)


@dataclass
class RescoreConfig:
    enabled: bool = False  # OFF by default — opt-in, optional dependency
    model: str = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
    max_latency_ms: int = 600
    context_hint: str = ""  # optional domain terms to anchor the correction


def rescore(text: str, config: RescoreConfig | None = None) -> str:
    """Repair semantically-implausible recognition errors, or return ``text``.

    Fast path: when disabled or the text is empty/whitespace, return it
    unchanged without importing mlx_lm. When enabled, run the model behind every
    safety guard; on ANY failure or suspicious output, return the original text.
    """
    if config is None:
        config = RescoreConfig()
    if not config.enabled or not text.strip():
        return text
    try:
        start = time.monotonic()
        candidate = _generate(text, config)
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > config.max_latency_ms:
            return text  # blew the latency budget; the original is safe
        cleaned = _sanitize(candidate)
        if not _is_safe(cleaned, text):
            return text
        return cleaned
    except Exception:
        # Never propagate: missing mlx_lm, model load failure, OOM, timeout, a
        # malformed response — all collapse to "use the original transcription".
        return text


def _build_prompt(text: str, config: RescoreConfig) -> str:
    hint = ""
    if config.context_hint.strip():
        hint = f"Domain terms that may appear: {config.context_hint.strip()}\n"
    return (
        f"{hint}Correct any speech recognition errors in the following dictated "
        f"text. Output only the corrected text.\n\n{text}"
    )


def _generate(text: str, config: RescoreConfig) -> str:
    """The single seam that talks to the model. Tests monkeypatch this.

    Lazily imports mlx_lm so the disabled path needs no dependency. Runs the
    instruction-tuned model deterministically (temperature 0) with the tight
    system prompt, and returns the raw model output (sanitizing/guarding is the
    caller's job).
    """
    from mlx_lm import generate, load  # deferred: optional dependency

    model, tokenizer = load(config.model)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(text, config)},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    return generate(model, tokenizer, prompt=prompt, temp=0.0, verbose=False)


def _sanitize(output: str) -> str:
    """Strip whitespace and a single layer of wrapping quotes the model may add."""
    cleaned = (output or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _is_safe(candidate: str, original: str) -> bool:
    """Reject empty output, runaway-length rewrites, and refusal/preamble text."""
    if not candidate:
        return False
    base = max(len(original), 1)
    if abs(len(candidate) - len(original)) / base > _MAX_LENGTH_DELTA:
        return False
    return not any(p.search(candidate) for p in _REFUSAL_PATTERNS)
