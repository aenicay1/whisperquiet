"""split_on_silence: VAD-style chunking of long buffers, no model involved."""

import numpy as np

from whisperquiet.audio import SAMPLE_RATE, _hard_split, split_on_silence

MAX_CHUNK_S = 25.0
MIN_CHUNK_S = 0.4


def _speech(seconds: float, amplitude: float = 0.1) -> np.ndarray:
    # 0.1 amplitude sine sits well above the 2e-4 silence threshold
    t = np.arange(int(SAMPLE_RATE * seconds), dtype=np.float32) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


def test_two_bursts_split_into_two_chunks():
    audio = np.concatenate([_speech(2.0), _silence(1.0), _speech(2.0)])
    chunks = split_on_silence(audio)
    assert len(chunks) == 2


def test_no_silence_is_one_chunk():
    chunks = split_on_silence(_speech(3.0))
    assert len(chunks) == 1
    np.testing.assert_array_equal(chunks[0], _speech(3.0))


def test_empty_input_returns_empty_list():
    assert split_on_silence(np.zeros(0, dtype=np.float32)) == []


def test_all_silence_returns_empty_list():
    assert split_on_silence(_silence(3.0)) == []


def test_long_monologue_hard_splits_below_max_chunk():
    # 40s of continuous tone with no silent gap must still be cut up
    audio = _speech(40.0)
    chunks = split_on_silence(audio)
    assert len(chunks) >= 2
    limit = int(SAMPLE_RATE * MAX_CHUNK_S)
    assert all(c.size <= limit for c in chunks)
    # the pieces should account for roughly the whole signal (padding aside)
    total = sum(c.size for c in chunks)
    assert abs(total - audio.size) <= SAMPLE_RATE  # within ~1s of slop


def test_sub_min_chunk_blip_merges_not_emitted_alone():
    # a 0.2s blip (< min_chunk_s) before a real burst must not stand alone
    blip = _speech(0.2)
    audio = np.concatenate([blip, _silence(1.0), _speech(2.0)])
    chunks = split_on_silence(audio)
    assert len(chunks) == 1
    assert all(c.size >= int(SAMPLE_RATE * MIN_CHUNK_S) for c in chunks)


def test_padding_and_trim_keep_chunk_count():
    audio = np.concatenate(
        [_silence(0.5), _speech(2.0), _silence(1.0), _speech(2.0), _silence(0.5)]
    )
    chunks = split_on_silence(audio)
    assert len(chunks) == 2
    # each chunk is roughly the 2s burst plus ~0.1s padding per side, never the
    # whole padded-out buffer
    for c in chunks:
        assert int(SAMPLE_RATE * 2.0) <= c.size <= int(SAMPLE_RATE * 2.5)


def test_single_loud_sample_is_one_chunk():
    chunks = split_on_silence(np.array([0.5], dtype=np.float32))
    assert len(chunks) == 1
    assert chunks[0].size == 1


def test_single_silent_sample_returns_empty():
    assert split_on_silence(np.array([0.0], dtype=np.float32)) == []


def test_all_nan_is_treated_as_silence():
    # NaN >= threshold is False, so an all-NaN buffer reads as silent and
    # yields no chunks rather than crashing or emitting garbage.
    audio = np.full(SAMPLE_RATE * 2, np.nan, dtype=np.float32)
    assert split_on_silence(audio) == []


def test_dc_offset_constant_never_silent_is_hard_split_and_bounded():
    # A constant non-zero DC bias has RMS above threshold everywhere: it is one
    # giant voiced span with no quiet point, so it must hard-split into bounded
    # pieces (not return a single 40s chunk, not loop forever).
    audio = np.full(int(SAMPLE_RATE * 40), 0.5, dtype=np.float32)
    chunks = split_on_silence(audio)
    limit = int(SAMPLE_RATE * MAX_CHUNK_S)
    assert len(chunks) >= 2
    assert all(0 < c.size <= limit for c in chunks)
    assert sum(c.size for c in chunks) == audio.size  # no samples dropped/duped


def test_hard_split_terminates_and_bounds_every_piece():
    # Pure tone with no interior trough, sizes straddling the boundary: every
    # piece must be non-empty, <= max_chunk, and cover the span exactly (proving
    # the cut always makes progress, never landing at 0 or span.size).
    window = int(SAMPLE_RATE * 0.03)
    for max_chunk in (window + 1, window * 3, int(SAMPLE_RATE * 25.0)):
        for size in (max_chunk + 1, max_chunk + window, max_chunk * 2 + 7):
            span = (0.1 * np.sin(np.arange(size) * 0.7)).astype(np.float32)
            pieces = _hard_split(span, window, max_chunk)
            assert all(0 < p.size <= max_chunk for p in pieces)
            assert sum(p.size for p in pieces) == size


def test_total_emitted_samples_are_reasonable():
    audio = np.concatenate([_speech(2.0), _silence(1.0), _speech(2.0)])
    chunks = split_on_silence(audio)
    total = sum(c.size for c in chunks)
    # ~4s of speech plus a little padding, well under the full 5s buffer
    assert int(SAMPLE_RATE * 4.0) <= total <= audio.size
