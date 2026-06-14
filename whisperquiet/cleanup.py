"""Rules-based dictation cleanup: stage 1 of docs/PLAN-dictation-cleanup.md.

Pure Python, no dependencies, zero model latency. Sits between the transcript
(final and streaming partials) and injection. Conservative by design: every
rule fires only on an exact cue, because a wrong "fix" in committed text is
worse than a leftover "um".

Pipeline order matters: corrections run before filler removal (the ", I mean,"
cue must resolve before aggressive mode strips "i mean" as a filler), repeats
collapse before spoken commands, and whitespace/punctuation normalization
always runs last.

Known heuristic limits, accepted on purpose:
- Correction cues drop the rejected text back to the previous clause boundary
  (comma, period, or start of text), so "Meet me Tuesday, no wait, Wednesday."
  becomes "Wednesday." Alignment smarter than that is stage-3 LLM territory.
- A filler set off by commas on both sides keeps one comma ("apples, um,
  bananas" -> "apples, bananas"), right for lists and tolerable elsewhere.
- Spoken commands fire only at a clause start (preceded by punctuation or the
  start of text), so "a new line of products" is safe, but an unpunctuated
  "first item new line second item" is missed.

Formatting stage (after the rules, before final whitespace normalization)
handles only clearly-mechanical, unambiguous fixes. Right now that is just time
formatting: Whisper writes times with a decimal point ("7.45pm"), and a period
is not a time separator, so we rewrite it to "7:45 PM". The fix fires only when
minutes are exactly two digits AND an am/pm marker is present, so money
("$18.00"), versions ("version 2.45"), and section numbers ("3.1") are never
touched. Currency/date/number reformatting is intentionally OUT OF SCOPE here:
spelled-vs-digit numbers, hyphenation, and oxford commas are preference, not
errors, and reformatting them would impose opinionated style. See
docs/BACKLOG.md for those preference-laden items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CleanupConfig:
    remove_fillers: bool = True
    fillers: tuple = ("um", "uh", "erm", "uhm", "mm-hmm", "hmm")
    aggressive: bool = False  # also strip AGGRESSIVE_FILLERS (phrase-aware)
    collapse_repeats: bool = True  # "the the the" -> "the"; kills hallucination loops
    resolve_corrections: bool = True
    spoken_commands: bool = True
    format_times: bool = True  # "7.45pm" -> "7:45 PM" (period is not a time separator)


AGGRESSIVE_FILLERS = ("like", "you know", "i mean", "sort of", "kind of")

# A single token repeating this many times at the very end of the transcript is
# a Whisper hallucination loop (see issue #1: ~250x "Ag"), not speech: drop it.
HALLUCINATION_TAIL_RUN = 10

# Runs of identical words/bigrams shorter than this are left alone, so legit
# doubles ("I had had enough") survive; >2 repeats collapse to one.
MIN_COLLAPSE_RUN = 3

_SENTENCE_END = (".", "!", "?", "\n")
_CLAUSE_BOUNDARIES = ".,!?;\n"

# Each cue requires its exact punctuation frame so prose like "no waiting
# allowed" or a bare "no wait times" never fires.
_CORRECTION_CUES = [
    re.compile(r",\s*no,?\s+wait\s*,\s*", re.IGNORECASE),  # X, no wait, Y
    re.compile(r"(?:\s*(?:—|–|--)\s*|\s+-\s+)no\s*,\s*", re.IGNORECASE),  # X — no, Y
    re.compile(r",\s*i\s+mean\s*,\s*", re.IGNORECASE),  # X, I mean, Y
    re.compile(r",\s*scratch\s+that\s*,\s*", re.IGNORECASE),  # X, scratch that, Y
    re.compile(r"(?:,\s*)?\bactually,?\s+make\s+that\s+", re.IGNORECASE),  # actually make that Y
]

# Times like "7.45pm" / "10.30 am" / "9.00 p.m." Whisper writes the separator
# as a period; rewrite to "7:45 PM". Fires only with exactly 2 minute digits AND
# an am/pm marker, so "$18.00", "version 2.45", and "section 3.1" never match.
# A word boundary on the left keeps us off "$18.00" (digit precedes nothing
# meaningful here) — the marker requirement is what actually protects money and
# versions, since neither carries am/pm.
_TIME_RE = re.compile(
    r"(?<![\w.])(?P<h>\d{1,2})\.(?P<m>\d{2})\s*(?P<mer>[ap])\.?m\.?\b",
    re.IGNORECASE,
)

# Commands must start a clause: preceded by punctuation, a newline, or the
# start of text. An optional trailing comma/period belongs to the command.
_COMMAND_RE = re.compile(
    r"(?:^|(?<=[\n.,;:!?]))\s*"
    r"(?:(?P<para>new\s+paragraph)|(?P<line>new\s+line|newline))\b[.,;:]?\s*",
    re.IGNORECASE,
)


def clean(text: str, config: CleanupConfig | None = None) -> str:
    if config is None:
        config = CleanupConfig()
    if not text.strip():
        return ""
    if config.resolve_corrections:
        text = _resolve_corrections(text)
    if config.remove_fillers:
        fillers = config.fillers + (AGGRESSIVE_FILLERS if config.aggressive else ())
        text = _remove_fillers(text, fillers)
    if config.collapse_repeats:
        text = _collapse_repeats(text)
    if config.spoken_commands:
        text = _spoken_commands(text)
    if config.format_times:
        text = _format_times(text)
    return _normalize(text)


# --- corrections ------------------------------------------------------------


def _resolve_corrections(text: str) -> str:
    """Resolve "X <cue> Y" by keeping Y and dropping X back to the previous
    clause boundary. Never reaches past a sentence boundary: the boundary scan
    stops at the first period/comma/etc., so earlier sentences are untouched.
    """
    for _ in range(10):  # bound the loop; real transcripts have a few cues at most
        match = None
        for cue in _CORRECTION_CUES:
            m = cue.search(text)
            if m and (match is None or m.start() < match.start()):
                match = m
        if match is None:
            break
        boundary = max(text.rfind(ch, 0, match.start()) for ch in _CLAUSE_BOUNDARIES)
        head = text[: boundary + 1]  # keeps the boundary char itself
        tail = text[match.end():]
        sentence_initial = boundary == -1 or text[boundary] in _SENTENCE_END
        dropped = text[boundary + 1 : match.end()].strip()
        if sentence_initial and dropped[:1].isupper():
            tail = tail[:1].upper() + tail[1:]
        sep = "" if not head or head.endswith((" ", "\n")) else " "
        text = head + sep + tail
    return text


# --- fillers ----------------------------------------------------------------


def _filler_regex(fillers: tuple) -> re.Pattern:
    parts = [r"\s+".join(re.escape(w) for w in f.split()) for f in fillers]
    parts.sort(key=len, reverse=True)  # longest first: "mm-hmm" before "hmm"
    # [\w-] lookarounds keep fillers out of other words: "umbrella" is safe,
    # and "hmm" never matches inside "mm-hmm".
    return re.compile(rf"(?<![\w-])(?:{'|'.join(parts)})(?![\w-])", re.IGNORECASE)


def _remove_fillers(text: str, fillers: tuple) -> str:
    if not fillers:
        return text
    pattern = _filler_regex(fillers)
    while True:
        m = pattern.search(text)
        if m is None:
            return text
        text = _excise(text, m.start(), m.end())


def _excise(text: str, start: int, end: int) -> str:
    """Remove a filler span and repair the joint around it."""
    left = text[:start].rstrip(" \t")
    right = text[end:].lstrip(" \t")
    if left == "" or left.endswith(_SENTENCE_END):
        # Sentence-initial filler: its trailing punctuation goes with it, and
        # the capitalization moves to the next word ("Um, so" -> "So").
        right = right.lstrip(".,;:!? \t")
        right = right[:1].upper() + right[1:]
        if left == "":
            return right
        return left + ("" if left.endswith("\n") else " ") + right
    if left.endswith(",") and right.startswith(","):
        right = right[1:].lstrip(" \t")  # ", um," -> one comma survives
    if right == "":
        return left.rstrip(",;: ")  # trailing filler: drop its lead-in comma too
    joiner = "" if right[0] in ".,;:!?\n" else " "
    return left + joiner + right


# --- repeats ----------------------------------------------------------------


def _key(token: str) -> str:
    return token.strip(".,;:!?").lower()


def _collapse_repeats(text: str) -> str:
    lines = []
    for line in text.split("\n"):  # per line, so existing newlines survive
        tokens = line.split()
        tokens = _drop_hallucination_tail(tokens)
        # unigrams first: a run of one word is otherwise eaten pairwise by the
        # bigram pass, which would leave two copies behind instead of one
        tokens = _collapse_ngram_runs(tokens, 1)
        tokens = _collapse_ngram_runs(tokens, 2)
        lines.append(" ".join(tokens))
    return "\n".join(lines)


def _drop_hallucination_tail(tokens: list[str]) -> list[str]:
    if not tokens:
        return tokens
    key = _key(tokens[-1])
    i = len(tokens)
    while i > 0 and _key(tokens[i - 1]) == key:
        i -= 1
    if len(tokens) - i >= HALLUCINATION_TAIL_RUN:
        return tokens[:i]
    return tokens


def _collapse_ngram_runs(tokens: list[str], n: int) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        unit = [_key(t) for t in tokens[i : i + n]]
        j = i + n
        while j + n <= len(tokens) and [_key(t) for t in tokens[j : j + n]] == unit:
            j += n
        if len(unit) == n and (j - i) // n >= MIN_COLLAPSE_RUN:
            kept = list(tokens[i : i + n])
            # keep the first occurrence (its capitalization) but carry trailing
            # punctuation from the last one: "No no no." -> "No."
            trail = re.search(r"[.,;:!?]+$", tokens[j - 1])
            if trail and not re.search(r"[.,;:!?]$", kept[-1]):
                kept[-1] += trail.group()
            out.extend(kept)
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return out


# --- spoken commands --------------------------------------------------------


def _spoken_commands(text: str) -> str:
    return _COMMAND_RE.sub(lambda m: "\n\n" if m.group("para") else "\n", text)


# --- formatting -------------------------------------------------------------


def _format_times(text: str) -> str:
    """Rewrite decimal-separated clock times ("7.45pm") to "7:45 PM".

    Mechanical and unambiguous: a period is never a valid time separator, and we
    only touch spans that carry an am/pm marker, so currency, version, and
    section numbers are left alone.
    """
    def repl(m: re.Match) -> str:
        return f"{m.group('h')}:{m.group('m')} {m.group('mer').upper()}M"

    return _TIME_RE.sub(repl, text)


# --- normalization ----------------------------------------------------------


def _normalize(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r",\s*(?=[.,!?;:])", "", text)  # ",." -> "." and ",," -> ","
    text = re.sub(r",(?=\n)", "", text)  # comma left behind by a command
    text = re.sub(r" (?=[.,;:!?])", "", text)
    # a spoken command starts a fresh line/paragraph: capitalize it
    text = re.sub(r"(\n+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text.strip()
