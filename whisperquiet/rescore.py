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
import threading
import time
from dataclasses import dataclass

# Length guard: if the rescored text differs from the input length by more than
# this fraction, treat it as a runaway rewrite and discard it. The polish is
# conservative — recognition fixes plus minor slips barely move the length, and
# rules cleanup already stripped fillers before this stage — so a large delta
# means a paraphrase/hallucination/big deletion, not a fix. Kept tight on
# purpose now that the prompt's mandate is broader than homophones-only.
_MAX_LENGTH_DELTA = 0.35

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
    "You clean up dictated text from a speech recognizer. Fix two kinds of "
    "errors: (1) recognition errors, where the recognizer heard a wrong word "
    "that sounds similar to the intended one, producing an implausible phrase; "
    "and (2) obvious dictation artifacts: false starts, accidentally repeated "
    "words, and clear grammatical slips. PRESERVE the speaker's exact meaning, "
    "wording, and tone. Do NOT rephrase for style, do NOT add or remove any "
    "substantive content, do NOT change the formality or register, and leave "
    "anything already correct exactly as is. When in doubt, leave it unchanged. "
    "Output ONLY the cleaned text with no preamble, quotes, or explanation."
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


# model repo -> (model, tokenizer), loaded once and kept resident. mlx_lm.load
# is expensive (seconds); reloading per utterance would blow the latency budget
# every single time, so cache it and let warm_up() pre-populate at app start.
_MODELS: dict = {}
_LOAD_LOCK = threading.Lock()


def _load(model: str):
    """Load and cache the instruction model. Deferred import keeps mlx_lm
    optional — the disabled path never reaches here. Double-checked locking so a
    startup warm_up and a worker-thread dictation can't both load it at once."""
    cached = _MODELS.get(model)
    if cached is not None:
        return cached
    with _LOAD_LOCK:
        if model not in _MODELS:  # re-check under the lock
            from mlx_lm import load  # deferred: optional dependency

            _MODELS[model] = load(model)
        return _MODELS[model]


def warm_up(config: RescoreConfig | None = None) -> None:
    """Pre-load the rescore model so the first real dictation isn't the cold
    one. Best-effort and never raises (mirrors the dictation backend warm_up);
    a no-op when rescoring is disabled."""
    config = config or RescoreConfig()
    if not config.enabled:
        return
    try:
        _load(config.model)
    except Exception:
        pass


def _generate(text: str, config: RescoreConfig) -> str:
    """The single seam that talks to the model. Tests monkeypatch this.

    Uses the cached (warm) model so only generation time counts against the
    latency budget. Caps max_tokens just above the input length: a correction
    is about as long as its input, so this bounds worst-case generation time
    and stops a runaway from eating the whole budget (the caller's post-hoc
    budget check then discards anything that still ran long). Returns the raw
    model output; sanitizing/guarding is the caller's job.
    """
    from mlx_lm import generate  # deferred: optional dependency

    model, tokenizer = _load(config.model)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_prompt(text, config)},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    max_tokens = min(512, int(len(text.split()) * 2.5) + 24)
    # force greedy (deterministic) decoding so the same input always yields the
    # same correction. Older mlx_lm takes temp=; if a version dropped the kwarg,
    # fall back to its default (also greedy) rather than break.
    try:
        return generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                        temp=0.0, verbose=False)
    except TypeError:
        return generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                        verbose=False)


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
