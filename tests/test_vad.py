"""Tests for the conservative speech-presence gate.

The gate's whole contract is: reject ONLY clear silence and clear single tones;
pass everything else (crucially low-energy / whispered / broadband speech), so
it can never swallow the audio the app cares most about.
"""
import numpy as np

from whisperquiet.audio import SAMPLE_RATE
from whisperquiet.vad import is_speech


def _sine(freq, seconds=0.5, amp=0.3, sr=SAMPLE_RATE):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_silence_is_not_speech():
    assert is_speech(np.zeros(SAMPLE_RATE, dtype=np.float32)) is False


def test_near_silence_below_floor_is_not_speech():
    rng = np.random.default_rng(1)
    faint = (rng.standard_normal(SAMPLE_RATE) * 1e-5).astype(np.float32)  # < floor
    assert is_speech(faint) is False


def test_whispered_range_broadband_passes():
    # THE contract test: broadband audio in the real whispered-speech RMS band
    # (~5e-5..1.5e-4) must PASS — the gate must never swallow quiet whisper.
    rng = np.random.default_rng(2)
    base = rng.standard_normal(SAMPLE_RATE)
    base /= np.sqrt(np.mean(base**2))  # unit RMS, then scale into the whisper band
    for target_rms in (6e-5, 1e-4, 1.4e-4):
        assert is_speech((base * target_rms).astype(np.float32)) is True, target_rms


def test_pure_tone_is_not_speech():
    # a steady single frequency (fan whine / electrical hum) — must be rejected
    assert is_speech(_sine(440)) is False
    assert is_speech(_sine(120)) is False  # low hum, still above the 60 Hz cutoff


def test_multitone_passes():
    # multiple strong components (formant-like) is NOT a single tone -> speech
    sig = _sine(220) + _sine(700) + _sine(2500)
    assert is_speech(sig.astype(np.float32)) is True


def test_low_energy_broadband_noise_passes():
    # low-amplitude broadband audio is the whisper-speech shape we must NOT gate
    rng = np.random.default_rng(0)
    quiet = (rng.standard_normal(SAMPLE_RATE) * 3e-3).astype(np.float32)
    assert is_speech(quiet) is True


def test_empty_and_too_short_are_not_speech():
    assert is_speech(np.zeros(0, dtype=np.float32)) is False
    assert is_speech(np.ones(8, dtype=np.float32)) is False
    assert is_speech(None) is False
