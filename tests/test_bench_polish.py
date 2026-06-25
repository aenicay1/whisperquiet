"""Pure-helper tests for the polish A/B harness (no model is loaded)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_polish as bp  # noqa: E402


def test_word_edits():
    assert bp._word_edits("a b c", "a b c") == 0
    assert bp._word_edits("a b c", "a x c") == 1  # one substitution
    assert bp._word_edits("a b c", "a c") == 1  # one deletion
    assert bp._word_edits("a c", "a b c") == 1  # one insertion


def test_change_summary_no_change():
    s = bp.change_summary("send it to the lawyers", "send it to the lawyers")
    assert s["changed"] is False
    assert s["word_edits"] == 0
    assert s["word_edit_frac"] == 0.0


def test_change_summary_small_change():
    s = bp.change_summary("send it to the warriors", "send it to the lawyers")
    assert s["changed"] is True
    assert s["word_edits"] == 1
    assert 0 < s["word_edit_frac"] < 0.5


def test_change_summary_whitespace_only_is_not_a_change():
    s = bp.change_summary("the figures rose", "  the figures rose  ")
    assert s["changed"] is False


def test_percentile():
    assert bp._percentile([], 50) == 0.0
    assert bp._percentile([5.0], 95) == 5.0
    assert bp._percentile([1.0, 2.0, 3.0], 50) == 2.0
    assert bp._percentile([1.0, 2.0, 3.0, 4.0], 75) == 3.25
