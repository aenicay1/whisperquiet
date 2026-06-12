"""trim_trailing_silence: cuts dead air after speech without eating quiet whispers."""

import numpy as np

from whisperquiet.audio import SAMPLE_RATE, trim_trailing_silence


def _speech(seconds: float, amplitude: float = 0.1) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * seconds), dtype=np.float32) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def test_speech_then_silence_trims_to_speech_plus_padding():
    speech = _speech(1.0)
    audio = np.concatenate([speech, np.zeros(SAMPLE_RATE * 2, dtype=np.float32)])
    out = trim_trailing_silence(audio)
    expected = speech.size + int(SAMPLE_RATE * 0.3)
    # within one 50ms analysis window of speech end + 0.3s padding
    assert abs(out.size - expected) <= int(SAMPLE_RATE * 0.05)
    np.testing.assert_array_equal(out, audio[: out.size])


def test_all_silence_returned_unchanged():
    audio = np.zeros(SAMPLE_RATE * 2, dtype=np.float32)
    out = trim_trailing_silence(audio)
    assert out.size == audio.size
    np.testing.assert_array_equal(out, audio)


def test_no_trailing_silence_roughly_unchanged():
    audio = _speech(1.5)
    out = trim_trailing_silence(audio)
    # padding is clamped to the array, so nothing should be lost
    assert out.size == audio.size
    np.testing.assert_array_equal(out, audio)


def test_short_arrays_do_not_crash():
    assert trim_trailing_silence(np.zeros(0, dtype=np.float32)).size == 0
    tiny_silent = np.zeros(10, dtype=np.float32)
    assert trim_trailing_silence(tiny_silent).size == 10
    tiny_loud = np.full(10, 0.1, dtype=np.float32)
    out = trim_trailing_silence(tiny_loud)
    assert 0 < out.size <= 10


def test_quiet_whisper_above_threshold_is_not_trimmed():
    # tail at 5e-4 RMS is above the 2e-4 default threshold: keep it
    speech = _speech(1.0)
    whisper_tail = np.full(SAMPLE_RATE, 5e-4, dtype=np.float32)
    audio = np.concatenate([speech, whisper_tail])
    out = trim_trailing_silence(audio)
    assert out.size == audio.size
    np.testing.assert_array_equal(out, audio)
