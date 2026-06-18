"""Denoiser A/B: WER with vs without a noise-suppression front-end.

The research says a denoiser is the cheapest single WER lever in noisy rooms,
but aggressive suppression can strip phonemes and HURT accuracy — so it must
earn its place per-environment, not by reputation. This runs the SAME recorded
takes through the SAME whisper backend twice — once on the raw audio, once after
whisperquiet/denoise.reduce_noise — and prints the WER delta so the adoption
decision is data-gated. It does not change the app; the denoiser is not wired
into the live path until a measured win here (see docs/BACKLOG.md).

Run it on NOISY takes (café / fan / AC) — on already-clean audio a denoiser has
nothing to remove and can only break even or hurt, which is the point of the
comparison.

Usage:
    bench_denoise.py refs.txt [audio_dir] [--takes N] [--non-stationary]
                      [--model REPO] [--show-text]

refs.txt: reference sentences, one per line; each WAV take is a full read of all
lines, scored against their concatenation (identical contract to bench_wer.py).
Install the denoiser with:  pip install -e '.[denoise]'
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_SCRIPT_DIR))

from bench_wer import wer  # noqa: E402  reuse the exact WER scorer

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"


def load_take(path: Path):
    """Load a WAV as mono float32 [-1, 1]. Asserts 16 kHz: the app records at
    16 kHz and the denoiser/transcribe path assumes it, so a stray-rate file
    should fail loudly rather than silently produce pitch-shifted nonsense."""
    import wave as wave_mod

    import numpy as np

    with wave_mod.open(str(path)) as w:
        rate = w.getframerate()
        if rate != 16_000:
            raise ValueError(f"{path.name}: expected 16 kHz WAV, got {rate} Hz")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("refs")
    ap.add_argument("audio_dir", nargs="?", default=str(
        Path.home() / "Library" / "Application Support" / "whisperquiet" / "audio"))
    ap.add_argument("--takes", type=int, default=2,
                    help="N most recent WAVs; each is a full read of refs")
    ap.add_argument("--non-stationary", action="store_true",
                    help="non-stationary noise model (default: stationary)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--show-text", action="store_true")
    args = ap.parse_args()

    from whisperquiet import denoise
    from whisperquiet.transcribe import transcribe_long

    if not denoise.available():
        print(f"denoise backend not installed — {denoise._INSTALL_HINT}")
        return 1

    refs = [l.strip() for l in open(args.refs) if l.strip()]
    full_ref = " ".join(refs)
    wavs = sorted(Path(args.audio_dir).glob("*.wav"),
                  key=lambda p: p.stat().st_mtime)[-args.takes:]
    if len(wavs) < args.takes:
        print(f"need {args.takes} wavs, found {len(wavs)} in {args.audio_dir}")
        return 1

    stationary = not args.non_stationary
    print(f"takes: {len(wavs)} most recent in {args.audio_dir}")
    print(f"refs:  {len(refs)} lines, {len(full_ref.split())} words")
    print(f"denoise: stationary={stationary}\n")

    head = f"{'take':<22}{'WER raw':>10}{'WER denoised':>14}{'delta':>9}"
    print(head)
    print("-" * len(head))
    raw_sum = den_sum = 0.0
    for path in wavs:
        audio = load_take(path)
        raw_text = transcribe_long(audio, args.model)
        den_audio = denoise.reduce_noise(audio, stationary=stationary)
        den_text = transcribe_long(den_audio, args.model)
        wer_raw = 100 * wer(full_ref, raw_text)
        wer_den = 100 * wer(full_ref, den_text)
        raw_sum += wer_raw
        den_sum += wer_den
        delta = wer_den - wer_raw
        print(f"{path.name:<22}{wer_raw:9.1f}%{wer_den:13.1f}%{delta:+8.1f}")
        if args.show_text:
            print(f"    raw: {raw_text}")
            print(f"    den: {den_text}")
    n = len(wavs)
    mean_raw, mean_den = raw_sum / n, den_sum / n
    print("-" * len(head))
    print(f"{'mean':<22}{mean_raw:9.1f}%{mean_den:13.1f}%{mean_den - mean_raw:+8.1f}")
    verdict = ("denoiser HELPS — consider wiring it in, behind a flag"
               if mean_den < mean_raw - 0.5
               else "denoiser HURTS — keep it off here"
               if mean_den > mean_raw + 0.5
               else "no meaningful difference")
    print(f"\n{verdict}. Record the verdict in docs/BACKLOG.md before wiring "
          "denoise into the live path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
