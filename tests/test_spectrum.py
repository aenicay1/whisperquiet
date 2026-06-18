"""spectrum_bands: log-spaced speech-band magnitudes; MicRecorder.recent tail-walk.

Pure tests — no AppKit, no GUI, no live mic.
"""

import numpy as np
import pytest

from whisperquiet.audio import SAMPLE_RATE, MicRecorder, spectrum_bands


def _tone(freq: float, seconds: float = 0.2, amplitude: float = 0.1) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * seconds), dtype=np.float64) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_returns_requested_length():
    bands = spectrum_bands(_tone(440.0), n_bands=24)
    assert len(bands) == 24
    bands = spectrum_bands(_tone(440.0), n_bands=8)
    assert len(bands) == 8


def test_values_within_unit_range():
    for amp in (0.01, 0.1, 1.0, 5.0):
        bands = spectrum_bands(_tone(300.0, amplitude=amp), n_bands=24)
        assert all(0.0 <= b <= 1.0 for b in bands)


def test_all_silence_is_all_zero():
    silence = np.zeros(int(SAMPLE_RATE * 0.2), dtype=np.float32)
    assert spectrum_bands(silence, n_bands=24) == [0.0] * 24


def test_empty_input_safe():
    assert spectrum_bands(np.zeros(0, dtype=np.float32), n_bands=24) == [0.0] * 24


def test_too_short_input_safe():
    assert spectrum_bands(np.ones(8, dtype=np.float32), n_bands=16) == [0.0] * 16


def test_none_input_safe():
    assert spectrum_bands(None, n_bands=10) == [0.0] * 10


def test_low_tone_loads_low_bands():
    bands = np.array(spectrum_bands(_tone(200.0), n_bands=24))
    half = len(bands) // 2
    assert bands[:half].sum() > bands[half:].sum()


def test_high_tone_loads_high_bands():
    bands = np.array(spectrum_bands(_tone(4000.0), n_bands=24))
    half = len(bands) // 2
    assert bands[half:].sum() > bands[:half].sum()


def test_quiet_speech_still_visible():
    # a quiet tone should still light up at least one band noticeably
    bands = spectrum_bands(_tone(250.0, amplitude=0.01), n_bands=24)
    assert max(bands) > 0.0


# -- MicRecorder.recent (no live mic; poke _chunks directly) -----------------


def _fill(rec: MicRecorder, n_samples: int, chunk: int = 1024) -> None:
    """Stuff fake (n,1) float32 chunks into the recorder's buffer."""
    pos = 0
    idx = 0
    while pos < n_samples:
        size = min(chunk, n_samples - pos)
        block = (np.arange(idx, idx + size, dtype=np.float32) % 100) / 100.0
        rec._chunks.append(block.reshape(-1, 1))
        pos += size
        idx += size


def test_recent_empty_when_no_audio():
    rec = MicRecorder()
    out = rec.recent(0.05)
    assert out.shape == (0,)
    assert out.dtype == np.float32


def test_recent_clamps_to_requested_window():
    rec = MicRecorder()
    _fill(rec, SAMPLE_RATE)  # 1s of audio
    out = rec.recent(0.05)
    assert out.size == int(SAMPLE_RATE * 0.05)
    assert out.ndim == 1


def test_recent_returns_all_when_shorter_than_window():
    rec = MicRecorder()
    _fill(rec, 500)  # fewer samples than a 0.05s window
    out = rec.recent(0.05)
    assert out.size == 500


def test_recent_returns_tail_not_head():
    rec = MicRecorder()
    # two distinct chunks; the tail window should come from the last one
    rec._chunks.append(np.zeros((SAMPLE_RATE, 1), dtype=np.float32))
    rec._chunks.append(np.ones((SAMPLE_RATE, 1), dtype=np.float32))
    out = rec.recent(0.05)
    assert np.allclose(out, 1.0)


def test_recent_feeds_spectrum():
    rec = MicRecorder()
    tone = _tone(220.0, seconds=0.5).reshape(-1, 1)
    rec._chunks.append(tone)
    bands = spectrum_bands(rec.recent(0.05), n_bands=24)
    assert len(bands) == 24
    assert all(0.0 <= b <= 1.0 for b in bands)
