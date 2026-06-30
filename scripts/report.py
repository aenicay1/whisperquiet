#!/usr/bin/env python3
"""Dogfood scorecard: today's stats + feedback against the DESIGN.md success bar.

Usage: report.py [stats.jsonl] [feedback.jsonl]
Paths default to the live CONFIG_DIR files; override for testing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisperquiet.feedback import FEEDBACK_PATH
from whisperquiet.stats import STATS_PATH

# Mirrors the kinds recorded by whisperquiet/vision/controller.py
GESTURE_KINDS = ["nod", "shake", "pause", "left_click", "double_click", "right_click", "scroll", "drag"]


def _percentile(values: list[int], q: float) -> float:
    """Linear-interpolated q-th percentile (q in 0..100) of a value list.

    Empty list -> 0.0. Used for the latency distribution, where summing (as the
    per-kind totals do) would be meaningless.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _load_events(path: Path, day: str) -> list[dict]:
    """All well-formed events from a jsonl file falling on the given local day."""
    events: list[dict] = []
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            event = json.loads(line)
            ts = event["ts"]
        except (ValueError, KeyError, TypeError):
            continue  # torn or foreign line; skip
        if datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == day:
            events.append(event)
    return events


def render(stats_path: Path, feedback_path: Path, day: str | None = None) -> str:
    """The scorecard as plain text for the given YYYY-MM-DD, today by default."""
    if day is None:
        day = datetime.now().strftime("%Y-%m-%d")
    stats_events = _load_events(stats_path, day)
    feedback_events = _load_events(feedback_path, day)

    totals: dict[str, int] = {}
    for event in stats_events:
        kind, n = event.get("kind"), event.get("n", 1)
        if isinstance(kind, str) and isinstance(n, int):
            totals[kind] = totals.get(kind, 0) + n

    # latency events carry the per-event value in n; collect the raw lists so we
    # can report the distribution (p50/p95) rather than a meaningless sum.
    commit_latencies = [
        e["n"] for e in stats_events
        if e.get("kind") == "commit_latency_ms" and isinstance(e.get("n"), int)
    ]
    transcribe_latencies = [
        e["n"] for e in stats_events
        if e.get("kind") == "transcribe_ms" and isinstance(e.get("n"), int)
    ]
    memory_values = {
        kind: [
            e["n"] for e in stats_events
            if e.get("kind") == kind and isinstance(e.get("n"), int)
        ]
        for kind in (
            "mlx_active_mb",
            "mlx_cache_mb",
            "mlx_peak_mb",
            "mlx_reclaimed_mb",
        )
    }

    dictations = totals.get("dictation", 0)
    words = totals.get("words", 0)
    touches = totals.get("trackpad_touch", 0)
    flags = sum(1 for e in feedback_events if e.get("kind") == "flag")
    edited = sum(1 for e in feedback_events if e.get("kind") == "dictation_edited")

    # Per-hour rates span first→last event of the day, floored at 1h.
    timestamps = [e["ts"] for e in stats_events + feedback_events]
    hours = max((max(timestamps) - min(timestamps)) / 3600.0, 1.0) if timestamps else 1.0
    edit_rate = (100.0 * edited / dictations) if dictations else 0.0
    flag_rate = flags / hours
    touch_rate = touches / hours

    flag_verdict = "PASS" if flag_rate < 1.0 else "FAIL"
    touch_verdict = "PASS" if touch_rate < 5.0 else "FAIL"

    lines = [
        f"whisperquiet dogfood scorecard — {day} (span {hours:.1f}h)",
        "",
        f"  dictations        {dictations:6d}",
        f"  words             {words:6d}",
        f"  dictation_edited  {edited:6d}  ({edit_rate:.0f}% of dictations)",
        f"  flags             {flags:6d}",
        f"  trackpad_touches  {touches:6d}",
        "",
        "gestures",
    ]
    for kind in GESTURE_KINDS:
        lines.append(f"  {kind:<16}  {totals.get(kind, 0):6d}")
    commit_p50 = _percentile(commit_latencies, 50)
    commit_p95 = _percentile(commit_latencies, 95)
    tx_p50 = _percentile(transcribe_latencies, 50)
    # the DESIGN.md commit target is <1s; judge on the median so one cold
    # outlier doesn't fail an otherwise-snappy day
    latency_verdict = (
        "  n/a" if not commit_latencies
        else "PASS" if commit_p50 < 1000 else "FAIL"
    )
    lines += [
        "",
        "latency (release -> committed text)",
        f"  commit p50        {commit_p50:6.0f} ms",
        f"  commit p95        {commit_p95:6.0f} ms",
        f"  transcribe p50    {tx_p50:6.0f} ms  (decode share of commit)",
        f"  samples           {len(commit_latencies):6d}",
        "",
        "memory (MLX / Metal)",
        f"  active latest     {_latest(memory_values['mlx_active_mb']):>6} MB",
        f"  cache latest      {_latest(memory_values['mlx_cache_mb']):>6} MB",
        f"  peak max          {_max_or_na(memory_values['mlx_peak_mb']):>6} MB",
        f"  cache reclaimed   {sum(memory_values['mlx_reclaimed_mb']):6d} MB",
        "",
        "targets (DESIGN.md success bar)",
        f"  commit p50                      {commit_p50:6.0f}  target <1000ms  {latency_verdict}",
        f"  flags/hour (false-click proxy)  {flag_rate:6.2f}  target <1/hour  {flag_verdict}",
        f"  trackpad_touches/hour           {touch_rate:6.2f}  target <5/hour  {touch_verdict}",
    ]
    return "\n".join(lines)


def _latest(values: list[int]) -> str:
    return str(values[-1]) if values else "n/a"


def _max_or_na(values: list[int]) -> str:
    return str(max(values)) if values else "n/a"


if __name__ == "__main__":
    stats_path = Path(sys.argv[1]) if len(sys.argv) > 1 else STATS_PATH
    feedback_path = Path(sys.argv[2]) if len(sys.argv) > 2 else FEEDBACK_PATH
    print(render(stats_path, feedback_path))
