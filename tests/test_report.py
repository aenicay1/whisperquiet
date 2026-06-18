"""Tests for the dogfood scorecard's latency reporting (percentiles, not sums)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import report  # noqa: E402


def test_percentile_edges_and_interpolation():
    assert report._percentile([], 50) == 0.0
    assert report._percentile([42], 95) == 42.0
    assert report._percentile([500, 800, 1200], 50) == 800.0
    assert report._percentile([500, 800, 1200], 95) == 1160.0


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_render_shows_latency_distribution_and_passes(tmp_path):
    now = int(time.time())
    stats = tmp_path / "stats.jsonl"
    feedback = tmp_path / "feedback.jsonl"
    events = [{"ts": now, "kind": "dictation", "n": 1}]
    for ms in (500, 800, 1200):
        events.append({"ts": now, "kind": "commit_latency_ms", "n": ms})
        events.append({"ts": now, "kind": "transcribe_ms", "n": ms - 100})
    _write_jsonl(stats, events)
    feedback.write_text("")

    out = report.render(stats, feedback)
    assert "latency (release -> committed text)" in out
    assert "commit p50" in out
    assert "800" in out  # p50 of 500/800/1200
    assert "target <1000ms  PASS" in out  # median 800 < 1s


def test_render_latency_fails_when_slow(tmp_path):
    now = int(time.time())
    stats = tmp_path / "stats.jsonl"
    feedback = tmp_path / "feedback.jsonl"
    _write_jsonl(stats, [{"ts": now, "kind": "commit_latency_ms", "n": 2500}])
    feedback.write_text("")
    out = report.render(stats, feedback)
    assert "target <1000ms  FAIL" in out


def test_render_latency_na_without_samples(tmp_path):
    now = int(time.time())
    stats = tmp_path / "stats.jsonl"
    feedback = tmp_path / "feedback.jsonl"
    _write_jsonl(stats, [{"ts": now, "kind": "dictation", "n": 1}])
    feedback.write_text("")
    out = report.render(stats, feedback)
    assert "n/a" in out
