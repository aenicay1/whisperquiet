import importlib.util
import json
import time
from pathlib import Path

from whisperquiet.feedback import FeedbackLog
from whisperquiet.stats import RECENT_CAP, SessionStats

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_report_module():
    spec = importlib.util.spec_from_file_location("report", REPO_ROOT / "scripts" / "report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feedback_log_round_trip(tmp_path):
    log = FeedbackLog(tmp_path / "feedback.jsonl")
    log.log("flag", {"recent": [{"kind": "left_click"}]})
    log.log("dictation_edited", {"words": 12, "keys": 7})
    log.log("flag")
    events = [json.loads(line) for line in (tmp_path / "feedback.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in events] == ["flag", "dictation_edited", "flag"]
    assert events[1]["detail"] == {"words": 12, "keys": 7}
    assert events[2]["detail"] == {}
    assert all(isinstance(e["ts"], int) for e in events)


def test_feedback_unwritable_path_never_raises(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")  # a file where a parent dir is needed
    log = FeedbackLog(blocker / "feedback.jsonl")
    log.log("flag")  # must not raise


def test_recent_ring_order(tmp_path):
    stats = SessionStats(tmp_path / "stats.jsonl")
    stats.record("dictation")
    stats.record("words", n=9)
    stats.record("left_click")
    assert [(e["kind"], e["n"]) for e in stats.recent()] == [
        ("dictation", 1),
        ("words", 9),
        ("left_click", 1),
    ]


def test_recent_ring_caps_at_16(tmp_path):
    stats = SessionStats(tmp_path / "stats.jsonl")
    for i in range(RECENT_CAP + 4):
        stats.record("words", n=i)
    recent = stats.recent()
    assert len(recent) == RECENT_CAP
    assert recent[0]["n"] == 4  # oldest four evicted
    assert recent[-1]["n"] == RECENT_CAP + 3


def test_recent_ring_filled_despite_unwritable_path(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    stats = SessionStats(blocker / "stats.jsonl")
    stats.record("left_click")
    assert [e["kind"] for e in stats.recent()] == ["left_click"]


def test_report_render_counts_and_targets(tmp_path):
    now = int(time.time())
    stats_path = tmp_path / "stats.jsonl"
    feedback_path = tmp_path / "feedback.jsonl"
    stats = SessionStats(stats_path)
    for _ in range(4):
        stats.record("dictation")
    stats.record("words", n=120)
    stats.record("nod")
    stats.record("left_click")
    stats.record("left_click")
    stats.record("trackpad_touch", n=3)
    log = FeedbackLog(feedback_path)
    log.log("flag", {"recent": []})
    log.log("flag")
    log.log("dictation_edited", {"words": 5, "keys": 3})

    report = _load_report_module()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    out = report.render(stats_path, feedback_path, day=day)

    # All events share ~one timestamp, so the span floors at 1h:
    # flags 2/h (FAIL vs <1/h), touches 3/h (PASS vs <5/h).
    assert "dictations" in out and "     4" in out
    assert "words" in out and "   120" in out
    assert "dictation_edited       1  (25% of dictations)" in out
    assert "flags                  2" in out
    assert "trackpad_touches       3" in out
    assert "nod                    1" in out
    assert "left_click             2" in out
    assert "shake                  0" in out
    assert "target <1/hour  FAIL" in out
    assert "target <5/hour  PASS" in out


def test_report_render_empty_files(tmp_path):
    report = _load_report_module()
    out = report.render(tmp_path / "stats.jsonl", tmp_path / "feedback.jsonl")
    assert "dictations" in out
    assert "PASS" in out  # zero rates pass both targets
    assert "FAIL" not in out
