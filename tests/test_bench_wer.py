"""Tests for bench_wer's reproducible baseline recorder (--json)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_wer as bw  # noqa: E402

_TAKES = [("a.wav", "ref", "hyp"), ("b.wav", "ref", "hyp")]


def test_records_lowest_wer_variant(tmp_path):
    path = tmp_path / "baseline.json"
    scored = [
        ("turbo gates norm", {}, 20.0),
        ("refine staccato", {"model": "repo/full"}, 5.49),
    ]
    bw._record_measurement(str(path), scored, _TAKES, "quiet")
    doc = json.loads(path.read_text())
    assert len(doc["measurements"]) == 1
    m = doc["measurements"][0]
    assert m["prompt"] == "refine staccato"  # the min-WER variant
    assert m["wer_pct"] == 5.5  # rounded
    assert m["model"] == "repo/full"
    assert m["condition"] == "quiet"
    assert m["n_takes"] == 2
    assert m["backend"] == "whisper"


def test_default_model_when_variant_has_none(tmp_path):
    path = tmp_path / "baseline.json"
    bw._record_measurement(str(path), [("turbo gates norm", {}, 7.0)], _TAKES, "cafe")
    m = json.loads(path.read_text())["measurements"][0]
    assert m["model"] == bw.TURBO


def test_appends_to_existing_baseline(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"measurements": [{"date": "2026-06-12", "wer_pct": 16.3}]}))
    bw._record_measurement(str(path), [("turbo gates norm", {}, 9.0)], _TAKES, "whisper")
    doc = json.loads(path.read_text())
    assert len(doc["measurements"]) == 2
    assert doc["measurements"][0]["wer_pct"] == 16.3  # preserved
    assert doc["measurements"][1]["condition"] == "whisper"
