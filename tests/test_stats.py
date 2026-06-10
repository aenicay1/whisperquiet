from datetime import datetime, timedelta

from whisperquiet.stats import SessionStats


def test_record_summary_round_trip(tmp_path):
    stats = SessionStats(tmp_path / "stats.jsonl")
    stats.record("left_click")
    assert stats.summary() == {"left_click": 1}


def test_multiple_kinds_and_n_aggregation(tmp_path):
    stats = SessionStats(tmp_path / "stats.jsonl")
    stats.record("words", n=12)
    stats.record("words", n=5)
    stats.record("dictation")
    stats.record("pause")
    stats.record("pause")
    assert stats.summary() == {"words": 17, "dictation": 1, "pause": 2}


def test_summary_of_empty_day_is_empty(tmp_path):
    stats = SessionStats(tmp_path / "stats.jsonl")
    stats.record("left_click")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert stats.summary(yesterday) == {}


def test_summary_with_no_file_is_empty(tmp_path):
    stats = SessionStats(tmp_path / "stats.jsonl")
    assert stats.summary() == {}


def test_unwritable_path_never_raises(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")  # a file where a parent dir is needed
    stats = SessionStats(blocker / "stats.jsonl")
    stats.record("left_click")  # must not raise
    assert stats.summary() == {}
