"""Tests for the optional denoiser seam.

No real noisereduce dependency is required: a fake module is injected at the
import seam (mirroring how test_bench_backends fakes the parakeet model).
"""
import sys
import types

import numpy as np
import pytest

from whisperquiet import denoise


def _install_fake_noisereduce(monkeypatch, recorder=None):
    fake = types.ModuleType("noisereduce")

    def reduce_noise(y, sr, stationary):
        if recorder is not None:
            recorder.update(sr=sr, stationary=stationary, dtype=y.dtype)
        return np.asarray(y) * 0.5  # arbitrary transform, distinct from input

    fake.reduce_noise = reduce_noise
    monkeypatch.setitem(sys.modules, "noisereduce", fake)
    return fake


def test_available_true_when_importable(monkeypatch):
    _install_fake_noisereduce(monkeypatch)
    assert denoise.available() is True


def test_available_false_when_missing(monkeypatch):
    # None in sys.modules makes `import noisereduce` raise ImportError
    monkeypatch.setitem(sys.modules, "noisereduce", None)
    assert denoise.available() is False


def test_reduce_noise_calls_backend_with_float32(monkeypatch):
    seen = {}
    _install_fake_noisereduce(monkeypatch, recorder=seen)
    audio = np.ones(1000, dtype=np.float64)  # deliberately not float32
    out = denoise.reduce_noise(audio, sample_rate=16_000, stationary=True)
    assert seen == {"sr": 16_000, "stationary": True, "dtype": np.dtype("float32")}
    assert out.dtype == np.float32
    assert np.allclose(out, 0.5)


def test_reduce_noise_empty_audio(monkeypatch):
    _install_fake_noisereduce(monkeypatch)
    out = denoise.reduce_noise(np.zeros(0, dtype=np.float32))
    assert out.size == 0


def test_reduce_noise_raises_without_backend(monkeypatch):
    monkeypatch.setitem(sys.modules, "noisereduce", None)
    with pytest.raises(ImportError):
        denoise.reduce_noise(np.ones(100, dtype=np.float32))
