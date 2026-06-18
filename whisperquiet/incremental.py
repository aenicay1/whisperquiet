"""Transcribe-as-you-go: lock each silence-bounded segment once, re-run only the tail.

The dictation loop snapshots the whole growing buffer every ~0.7s and again on
release. Re-decoding that entire buffer each tick is O(n^2) work whose release
latency grows with dictation length, and the streaming preview (a separate pass)
disagrees with the final committed text.

IncrementalTranscriber fixes both: it transcribes each silence-bounded segment
exactly ONCE, locks the resulting text, and only re-decodes the still-live tail
after the last locked boundary. The same locked text is what finalize() commits,
so preview and final never diverge.

Pure, deterministic, and model-free: segmentation derives only from the audio
array (never wall clock), and transcription is an injected callable so the model
and vocab live entirely in the caller. Never imports mlx_whisper.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE, split_on_silence, trim_trailing_silence


class IncrementalTranscriber:
    """Stateful coordinator over one dictation's growing audio buffer.

    The caller passes the whole snapshot to update() repeatedly while PTT is
    held, then finalize() once on release. Samples before ``_finalized_end`` are
    already decoded into ``_locked`` and are never re-transcribed; only the live
    tail after that cursor is decoded fresh each call.
    """

    def __init__(
        self,
        transcribe_fn: Callable[[np.ndarray], str],
        sample_rate: int = SAMPLE_RATE,
        min_silence_s: float = 0.6,
        silence_thresh: float = 2e-4,
        min_finalize_s: float = 0.8,
    ) -> None:
        self._transcribe_fn = transcribe_fn
        self._sample_rate = sample_rate
        self._min_silence_s = min_silence_s
        self._silence_thresh = silence_thresh
        self._min_finalize_s = min_finalize_s
        self.reset()

    def reset(self) -> None:
        """Clear all per-dictation state so the next dictation starts clean."""
        self._locked: list[str] = []
        self._finalized_end: int = 0
        self._prev_size: int = 0

    def update(self, full_audio: np.ndarray) -> str:
        """Lock any newly-completed segments, then decode the live tail fresh.

        A segment after ``_finalized_end`` is "complete" only once it is followed
        by >= min_finalize_s of trailing silence — i.e. the user has clearly moved
        past it. Each such segment is transcribed ONCE, appended to ``_locked``,
        and the cursor advanced past it. The remaining tail is decoded fresh (it
        may still change as more audio arrives) and is NOT locked. Returns the
        joined preview ``" ".join(_locked + [tail_text])``.
        """
        self._sync(full_audio)
        self._lock_complete_segments(full_audio, require_finalize_silence=True)
        tail_text = self._transcribe_tail(full_audio)
        return self._join(tail_text)

    def finalize(self, full_audio: np.ndarray) -> str:
        """Commit the dictation: lock all remaining complete segments, then the
        final tail (whatever follows the cursor, trimmed). Returns the full joined
        text. The object stays reset-able for the next dictation via reset()."""
        self._sync(full_audio)
        # On release there is no "user moved on" requirement: every interior
        # segment up to the final voiced span is complete.
        self._lock_complete_segments(full_audio, require_finalize_silence=False)
        tail_text = self._transcribe_tail(full_audio)
        if tail_text:
            self._locked.append(tail_text)
        # The whole tail region has now been considered and committed (its text,
        # if any, is locked). Advance the cursor to the end unconditionally so the
        # post-condition "finalize leaves _finalized_end == buffer size" holds even
        # when the final span decoded to nothing — otherwise a later call on the
        # same (un-reset) object would re-decode an already-finalized tail.
        self._finalized_end = full_audio.size
        return " ".join(self._locked)

    # --- internals -------------------------------------------------------

    def _sync(self, full_audio: np.ndarray) -> None:
        """Guard the grow-only contract. The buffer should only ever grow within
        a dictation; if it shrank (a fresh dictation reused the object without
        reset, say) start over rather than index past the end or emit stale text.
        """
        if full_audio.size < self._prev_size:
            self.reset()
        self._prev_size = full_audio.size

    def _lock_complete_segments(
        self, full_audio: np.ndarray, require_finalize_silence: bool
    ) -> None:
        """Walk the region after the cursor, locking each segment that ends at a
        real (>= min_silence_s) pause. With require_finalize_silence, a segment is
        locked only when followed by >= min_finalize_s of trailing silence, so a
        segment the user is still mid-pause on stays in the live tail."""
        while True:
            end = self._next_segment_end(full_audio, require_finalize_silence)
            if end is None:
                return
            segment = full_audio[self._finalized_end : end]
            text = self._transcribe_fn(segment)
            if text:
                self._locked.append(text)
            self._finalized_end = end

    def _next_segment_end(
        self, full_audio: np.ndarray, require_finalize_silence: bool
    ) -> int | None:
        """Absolute end index of the first complete segment after the cursor, or
        None if the tail has no safely-complete segment yet.

        We slice the post-cursor region and let split_on_silence find the voiced
        spans with the same VAD semantics as the long-form path. There is a
        complete segment only when split yields >= 2 spans (a span followed by a
        real pause and more voiced audio) — or, when finalizing, the first of any
        such spans. The cut sits in the silent gap after the first span; with
        require_finalize_silence we additionally demand min_finalize_s of silence
        right after that span before committing.
        """
        region = full_audio[self._finalized_end :]
        if region.size == 0:
            return None
        end_in_region = self._first_gap(region)
        if end_in_region is None:
            return None
        if require_finalize_silence:
            trailing = self._trailing_silence(region, end_in_region)
            if trailing < int(self._sample_rate * self._min_finalize_s):
                return None
        return self._finalized_end + end_in_region

    def _first_gap(self, region: np.ndarray) -> int | None:
        """Index within ``region`` where the first complete segment ends — i.e.
        the start of the first >= min_silence_s silent gap that has more voiced
        audio after it. Returns None when there is no such interior pause (the
        whole region is at most one ongoing span). Mirrors split_on_silence's
        window/RMS/min_silence math so boundaries agree with the long-form path.
        """
        # split_on_silence collapses voiced windows into spans only when the
        # silent run between them reaches min_silence; reuse it to confirm the
        # region actually contains more than one span before we bother locating
        # the gap. One span (or none) => nothing complete yet.
        if len(split_on_silence(
            region,
            sample_rate=self._sample_rate,
            min_silence_s=self._min_silence_s,
            silence_thresh=self._silence_thresh,
        )) < 2:
            return None
        window = max(1, int(self._sample_rate * 0.03))
        min_silence = int(self._sample_rate * self._min_silence_s)
        starts = range(0, region.size, window)
        voiced = [
            float(
                np.sqrt(
                    np.mean(np.square(region[s : s + window], dtype=np.float64))
                )
            )
            >= self._silence_thresh
            for s in starts
        ]
        # find the end of the first voiced span followed by a >= min_silence gap
        # that is itself followed by more voiced audio
        seen_voiced = False
        span_end = 0
        silent_run = 0
        for i, is_voiced in enumerate(voiced):
            if is_voiced:
                if seen_voiced and silent_run * window >= min_silence:
                    return span_end
                seen_voiced = True
                silent_run = 0
                span_end = min(region.size, i * window + window)
            else:
                silent_run += 1
        return None

    def _trailing_silence(self, region: np.ndarray, frm: int) -> int:
        """Count samples of contiguous silence in ``region`` starting at ``frm``
        (the end of a voiced span), using the same window RMS as the splitter."""
        window = max(1, int(self._sample_rate * 0.03))
        count = 0
        pos = frm
        while pos < region.size:
            chunk = region[pos : pos + window]
            rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
            if rms >= self._silence_thresh:
                break
            count += chunk.size
            pos += window
        return count

    def _transcribe_tail(self, full_audio: np.ndarray) -> str:
        """Decode the live tail after the cursor, fresh. Trim trailing silence so
        the model never decodes into dead air (matching the transcribe() path)."""
        tail = full_audio[self._finalized_end :]
        if tail.size == 0:
            return ""
        tail = trim_trailing_silence(
            tail, sample_rate=self._sample_rate, threshold=self._silence_thresh
        )
        return self._transcribe_fn(tail)

    def _join(self, tail_text: str) -> str:
        parts = list(self._locked)
        if tail_text:
            parts.append(tail_text)
        return " ".join(parts)
