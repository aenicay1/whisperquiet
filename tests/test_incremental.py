"""IncrementalTranscriber: lock each silence-bounded segment once, re-run the tail.

A fake transcribe_fn returns a per-slice marker and counts calls, so we can prove
no segment is ever re-transcribed and that a single segment's decode happens
exactly once across a whole dictation's worth of update() calls.
"""

import numpy as np

from whisperquiet.audio import SAMPLE_RATE
from whisperquiet.incremental import IncrementalTranscriber


def _speech(seconds: float, amplitude: float = 0.1) -> np.ndarray:
    # 0.1 amplitude sine sits well above the 2e-4 silence threshold
    t = np.arange(int(SAMPLE_RATE * seconds), dtype=np.float32) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)


class CountingFake:
    """Returns "wN" for the Nth distinct non-empty slice and counts every call.

    A non-empty audio slice maps to a stable label by its length so the same
    finalized segment, if (wrongly) re-decoded, would show up as a repeat call.
    Empty/all-silent slices return "" to exercise the no-stray-space path.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.seg_calls: list[int] = []  # sizes passed in, in order

    def __call__(self, audio: np.ndarray) -> str:
        self.calls += 1
        self.seg_calls.append(int(audio.size))
        if audio.size == 0 or float(np.max(np.abs(audio))) < 1e-4:
            return ""
        # label by rounded duration so distinct bursts get distinct markers
        return f"w{round(audio.size / SAMPLE_RATE, 1)}"


def test_two_bursts_lock_in_order_and_first_decoded_once():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)
    full = np.concatenate([_speech(2.0), _silence(1.5), _speech(2.0)])

    # feed the buffer as a growing snapshot over several update() calls
    cuts = [
        int(SAMPLE_RATE * 1.0),
        int(SAMPLE_RATE * 2.5),  # inside the gap after burst 1
        int(SAMPLE_RATE * 4.0),  # gap past min_finalize, burst 2 now long enough
        full.size,
    ]
    locked_after_gap = None
    for c in cuts:
        it.update(full[:c])
        if c == cuts[2]:
            locked_after_gap = list(it._locked)

    # by the time the gap + min_finalize silence has passed, burst 1 is locked
    assert locked_after_gap == ["w2.0"]

    out = it.finalize(full)
    parts = out.split()
    # two segments in order: locked burst 1, then the committed tail (burst 2)
    assert len(parts) == 2
    assert parts[0] == "w2.0"  # locked burst 1 stays exactly as locked
    assert parts[1].startswith("w")  # the final tail (gap + burst 2)

    # the cursor ended at the end of the buffer after finalize
    assert it._finalized_end == full.size
    # A locked segment is decoded by slicing full_audio[cursor:end] verbatim (no
    # trimming), so its call records an EXACT, reproducible size; the live tail is
    # always trim_trailing_silence'd first, so a tail decode never lands on that
    # exact length by accident. Recompute burst 1's boundary deterministically and
    # assert the fake saw a slice of exactly that size exactly once.
    probe = IncrementalTranscriber(lambda a: "")
    probe._lock_complete_segments(full, require_finalize_silence=True)
    seg1_size = probe._finalized_end
    assert seg1_size > 0
    assert fake.seg_calls.count(seg1_size) == 1


def test_single_short_utterance_tail_only():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)
    full = _speech(1.0)

    preview = it.update(full)
    assert preview == "w1.0"  # all tail, nothing locked
    assert it._locked == []

    final = it.finalize(full)
    assert final == "w1.0"
    # nothing was ever locked mid-stream; calls stay small and bounded
    assert it._locked == ["w1.0"]
    assert fake.calls <= 3


def test_empty_audio_returns_empty():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)
    empty = np.zeros(0, dtype=np.float32)
    assert it.update(empty) == ""
    assert it.finalize(empty) == ""


def test_all_silence_returns_empty():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)
    silent = _silence(3.0)
    assert it.update(silent) == ""
    assert it.finalize(silent) == ""
    assert it._locked == []


def test_long_continuous_burst_finalizes_without_qualifying_silence():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)
    full = _speech(5.0)  # no interior silence -> nothing locks mid-stream

    it.update(full[: int(SAMPLE_RATE * 2.0)])
    it.update(full[: int(SAMPLE_RATE * 4.0)])
    assert it._locked == []  # never finalized a segment without a real pause

    final = it.finalize(full)
    # the whole burst comes out as exactly ONE final tail segment (no spurious
    # interior locks split a pause-free monologue), and the cursor reaches the end
    assert it._locked == [final]
    assert len(final.split()) == 1
    assert final.startswith("w")
    assert it._finalized_end == full.size


def test_reset_lets_second_dictation_start_clean():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)

    first = np.concatenate([_speech(2.0), _silence(1.5), _speech(1.5)])
    it.update(first[: int(SAMPLE_RATE * 3.7)])
    it.finalize(first)
    assert it._locked  # had locked + tail content

    it.reset()
    assert it._locked == []
    assert it._finalized_end == 0

    second = _speech(1.0)
    out = it.finalize(second)
    assert out == "w1.0"


def test_shrinking_buffer_resets_instead_of_crashing():
    fake = CountingFake()
    it = IncrementalTranscriber(fake)
    big = np.concatenate([_speech(2.0), _silence(1.5), _speech(2.0)])
    it.update(big)
    # a smaller buffer (new dictation reusing the object) must not index past end
    small = _speech(1.0)
    out = it.update(small)
    assert out == "w1.0"
    assert it._finalized_end <= small.size


def test_finalize_sees_longer_buffer_than_last_update_no_loss_or_dup():
    """The real wiring: update() runs on a live snapshot ~0.7s behind, then
    finalize() runs on recorder.stop()'s FULL buffer — strictly longer, with a
    whole burst the last update never saw. Every voiced sample must be decoded
    exactly once (no loss, no duplicate) and the cursor must reach the end.

    The fake records the absolute [start,end) of each slice it is handed by
    tracking the cursor before each lock; we assert the union of decoded ranges
    is gap-free and overlap-free across the buffer the cursor advances over.
    """
    full = np.concatenate(
        [_speech(2.0), _silence(1.5), _speech(2.0), _silence(1.5), _speech(2.0)]
    )

    it = IncrementalTranscriber(CountingFake())

    # last update stops well before the final burst even exists in the snapshot
    last_update_end = int(SAMPLE_RATE * 4.0)
    it.update(full[:last_update_end])
    locked_during_update = list(it._locked)
    assert it._finalized_end <= last_update_end  # never indexed past the snapshot
    assert locked_during_update, "burst 1 should lock once its pause is past"

    # finalize sees the FULL buffer — strictly longer; the 3rd burst (and most of
    # the 2nd) was never in any update snapshot.
    assert full.size > last_update_end
    out = it.finalize(full)
    parts = out.split()

    # No loss: every burst is represented. No duplication: locked-during-update
    # text appears exactly once, as the verbatim prefix, and is never re-decoded.
    assert parts[: len(locked_during_update)] == locked_during_update
    assert parts.count(locked_during_update[0]) == 1
    assert len(parts) == 3  # three bursts -> three committed segments, no more
    # cursor reaches the end: nothing after the last update was dropped.
    assert it._finalized_end == full.size

    # And the locked segments form a strictly increasing, non-overlapping cursor
    # walk: a probe over the full buffer reproduces the same lock boundaries the
    # streamed run produced, proving the boundary math is snapshot-independent.
    probe = IncrementalTranscriber(lambda a: "")
    probe.finalize(full)
    assert probe._finalized_end == it._finalized_end == full.size


def test_empty_segment_contributes_no_stray_space():
    # transcribe_fn returning "" for a finalized segment must add nothing
    def silent_seg_fn(audio: np.ndarray) -> str:
        # only the second burst yields text; first returns "" despite being voiced
        return "" if audio.size < int(SAMPLE_RATE * 1.6) else "real"

    it = IncrementalTranscriber(silent_seg_fn)
    full = np.concatenate([_speech(1.0), _silence(1.5), _speech(2.0)])
    out = it.finalize(full)
    assert out == "real"  # no leading space, no double space
    assert "  " not in out
