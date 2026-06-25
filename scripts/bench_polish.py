"""Offline A/B for the on-device LLM polish stage (whisperquiet/rescore.py).

Runs the real rescore model over the dictation corpus (transcripts.jsonl, the
raw->clean pairs the app already saves) and reports, so the enable decision is
data-driven, not a guess:

    * latency       generation ms per utterance (p50/p95) -> sets the live budget
    * applied %      how often the safety guards KEEP the polish vs discard it
    * changed %      of applied, how often it actually changed the text
    * the diffs      every changed item printed before->after, and large edits
                     flagged REVIEW, so a human can confirm meaning was preserved

Meaning-preservation is NOT auto-scored — that needs eyes. This surfaces exactly
what changed so you can judge it before flipping config.rescore_enabled on.

The polish input is the `clean` field (what cleanup.py produced), because that
is what rescore sees in the live pipeline (raw -> cleanup -> rescore).

Usage:
    bench_polish.py [transcripts.jsonl] [--limit N] [--model REPO] [--vocab "..."]
Needs the optional dependency:  pip install -e '.[rescore]'
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _word_edits(a: str, b: str) -> int:
    """Word-level Levenshtein distance between two strings."""
    x, y = a.split(), b.split()
    prev = list(range(len(y) + 1))
    for i, xi in enumerate(x, 1):
        cur = [i]
        for j, yj in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (xi != yj)))
        prev = cur
    return prev[len(y)]


def change_summary(before: str, after: str) -> dict:
    """Pure change metrics between the polish input and output."""
    edits = _word_edits(before, after)
    nwords = max(len(before.split()), 1)
    blen = max(len(before), 1)
    return {
        "changed": before.strip() != after.strip(),
        "word_edits": edits,
        "word_edit_frac": edits / nwords,
        "len_delta_frac": (len(after) - len(before)) / blen,
    }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] * (1 - (pos - lo)) + s[hi] * (pos - lo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcripts", nargs="?", default=str(
        Path.home() / "Library" / "Application Support" / "whisperquiet"
        / "transcripts.jsonl"))
    ap.add_argument("--limit", type=int, default=40,
                    help="most-recent N pairs to run (each is one model call)")
    ap.add_argument("--model", default=None, help="override the rescore model repo")
    ap.add_argument("--vocab", default="", help="context_hint domain terms")
    ap.add_argument("--review-frac", type=float, default=0.25,
                    help="flag items whose word-edit fraction exceeds this")
    args = ap.parse_args()

    from whisperquiet import rescore as rs

    cfg = rs.RescoreConfig(enabled=True, context_hint=args.vocab,
                           max_latency_ms=10_000_000)  # don't let the budget hide latency
    if args.model:
        cfg.model = args.model

    lines = Path(args.transcripts).read_text().splitlines()
    inputs: list[str] = []
    for line in lines:
        try:
            d = json.loads(line).get("detail", {})
        except ValueError:
            continue
        text = (d.get("clean") or d.get("raw") or "").strip()
        if text:
            inputs.append(text)
    inputs = inputs[-args.limit:]
    if not inputs:
        print(f"no usable transcript pairs in {args.transcripts}")
        return 1

    print(f"polish A/B: {len(inputs)} utterances, model={cfg.model}")
    print("warming model …", flush=True)
    rs.warm_up(cfg)

    latencies, applied, changed, flagged = [], 0, 0, []
    changes = []
    for text in inputs:
        t0 = time.perf_counter()
        try:
            raw = rs._generate(text, cfg)
        except Exception as exc:
            print(f"  generate failed ({type(exc).__name__}: {exc}) — is mlx-lm installed?")
            return 1
        latencies.append((time.perf_counter() - t0) * 1000)
        sanitized = rs._sanitize(raw)
        safe = rs._is_safe(sanitized, text)
        out = sanitized if safe else text
        if safe:
            applied += 1
        summ = change_summary(text, out)
        if summ["changed"]:
            changed += 1
            changes.append((text, out, summ, safe))
            if summ["word_edit_frac"] > args.review_frac:
                flagged.append((text, out, summ))

    n = len(inputs)
    print(f"\nlatency  p50={_percentile(latencies,50):.0f}ms  "
          f"p95={_percentile(latencies,95):.0f}ms  "
          f"max={max(latencies):.0f}ms")
    print(f"applied  {applied}/{n} ({100*applied/n:.0f}%)  "
          f"(rest discarded by safety guards)")
    print(f"changed  {changed}/{n} ({100*changed/n:.0f}%)  "
          f"flagged-for-review {len(flagged)}")

    print("\n--- changes (eyeball meaning preservation) ---")
    for before, after, summ, safe in changes[:40]:
        tag = "FLAG" if summ["word_edit_frac"] > args.review_frac else "    "
        print(f"[{tag}] -{before}\n       +{after}")
    print("\nDecision guide: low p50 latency + only meaning-preserving changes "
          "(no FLAG surprises) => raise config.max_latency_ms accordingly and "
          "consider enabling. Any meaning change => keep OFF / tighten the prompt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
