"""Pure-logic tests for the backend selector, the parakeet backend's optional-
dependency boundary, and the bench_backends scoring/plumbing helpers.

No model is ever loaded: parakeet_mlx is replaced at the _load seam (mirroring
test_rescore's _generate seam), and the bench helpers are pure functions.
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from whisperquiet import backends
from whisperquiet import config as config_mod
from whisperquiet import parakeet as parakeet_mod
from whisperquiet import transcribe as whisper_mod
from whisperquiet.config import Config

# bench_backends lives in scripts/, not the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_backends as bb  # noqa: E402


# --- backend selector ------------------------------------------------------ #
def test_get_backend_defaults_to_whisper():
    backend, repo = backends.get_backend(Config())
    assert backend is whisper_mod
    assert repo == config_mod.ACCURACY_MODEL_REPO


def test_get_backend_parakeet_when_flag_set():
    cfg = Config(dictation_backend="parakeet")
    backend, repo = backends.get_backend(cfg)
    assert backend is parakeet_mod
    assert repo == cfg.parakeet_repo


def test_get_backend_unknown_value_falls_back_to_whisper():
    backend, repo = backends.get_backend(Config(dictation_backend="bogus"))
    assert backend is whisper_mod
    assert repo == config_mod.ACCURACY_MODEL_REPO


# --- parakeet optional-dependency boundary --------------------------------- #
def test_parakeet_skips_short_audio_without_importing_dependency(monkeypatch):
    # Below the min-audio guard the backend must return "" before _load, so the
    # optional dependency is never touched (the disabled path costs nothing).
    monkeypatch.setattr(parakeet_mod, "_load",
                        lambda repo: pytest.fail("_load must not run for short audio"))
    monkeypatch.delitem(sys.modules, "parakeet_mlx", raising=False)
    assert parakeet_mod.transcribe(np.zeros(100, dtype=np.float32), "repo") == ""
    assert "parakeet_mlx" not in sys.modules


def test_parakeet_transcribe_writes_wav_and_returns_model_text(monkeypatch):
    seen = {}

    class FakeResult:
        text = "  Hello from Parakeet.  "

    class FakeModel:
        def transcribe(self, path, **kwargs):
            seen["path"] = path
            seen["exists"] = Path(path).exists()
            seen["kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(parakeet_mod, "_load", lambda repo: FakeModel())
    audio = np.full(16_000, 0.5, dtype=np.float32)  # 1s, above the guard
    out = parakeet_mod.transcribe(audio, "repo", vocabulary=["Springdale"])

    assert out == "Hello from Parakeet."  # stripped
    assert seen["exists"] and seen["path"].endswith(".wav")
    assert seen["kwargs"] == {}  # short clip: no chunking kwargs


def test_parakeet_long_audio_enables_native_chunking(monkeypatch):
    seen = {}
    monkeypatch.setattr(parakeet_mod, "_write_wav", lambda audio, path: None)

    class FakeModel:
        def transcribe(self, path, **kwargs):
            seen["kwargs"] = kwargs
            return type("R", (), {"text": "ok"})()

    monkeypatch.setattr(parakeet_mod, "_load", lambda repo: FakeModel())
    long_audio = np.full(int(16_000 * 90), 0.5, dtype=np.float32)  # 90s
    parakeet_mod.transcribe(long_audio, "repo")
    assert "chunk_duration" in seen["kwargs"]
    assert "overlap_duration" in seen["kwargs"]


def test_parakeet_write_wav_roundtrips(tmp_path):
    path = str(tmp_path / "x.wav")
    audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    parakeet_mod._write_wav(audio, path)
    with wave.open(path) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16_000
        back = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert np.allclose(back.astype(np.float32) / 32767.0, audio, atol=1e-3)


# --- bench_backends pure helpers ------------------------------------------- #
def test_human_bytes():
    assert bb.human_bytes(None) == "—"
    assert bb.human_bytes(0) == "0 B"
    assert bb.human_bytes(2048).endswith("KB")
    assert bb.human_bytes(int(1.70 * 1024**3)).startswith("1.70 GB")


def test_rtf():
    assert bb.rtf(0.2, 20.0) == pytest.approx(0.01)
    assert bb.rtf(1.0, 0.0) == 0.0


def test_worker_cmd_shape():
    cmd = bb.worker_cmd("py", "parakeet", "repo/x", "en", ["a.wav", "b.wav"])
    assert cmd[0] == "py"
    assert "--_worker" in cmd and "parakeet" in cmd
    assert cmd[cmd.index("--model") + 1] == "repo/x"
    assert cmd[-2:] == ["a.wav", "b.wav"]


def test_extract_json_picks_marker_line_and_ignores_noise():
    stdout = (
        "Fetching model files: 100%\n"
        + bb.MARKER + json.dumps({"backend": "whisper", "takes": []}) + "\n"
        "trailing library chatter\n"
    )
    assert bb.extract_json(stdout)["backend"] == "whisper"


def test_extract_json_raises_when_absent():
    with pytest.raises(ValueError):
        bb.extract_json("no marker here\njust noise\n")


def test_summarize_uses_shared_wer_scorer():
    full_ref = "the quarterly report is due friday"
    result = {
        "load_s": 3.0,
        "peak_mlx_bytes": 1000,
        "peak_rss_bytes": 2000,
        "takes": [
            {"wav": "a.wav", "audio_s": 10.0, "decode_s": 1.0,
             "text": "the quarterly report is due friday"},   # perfect → 0%
            {"wav": "b.wav", "audio_s": 10.0, "decode_s": 3.0,
             "text": "the quarterly report"},                  # 3 of 6 missing
        ],
    }
    s = bb.summarize(result, full_ref)
    assert s["takes"][0]["wer"] == pytest.approx(0.0)
    assert s["takes"][1]["wer"] == pytest.approx(50.0)
    assert s["mean_wer"] == pytest.approx(25.0)
    assert s["mean_latency"] == pytest.approx(2.0)
    assert s["mean_rtf"] == pytest.approx(0.2)  # (0.1 + 0.3) / 2
    assert s["peak_mlx_bytes"] == 1000
