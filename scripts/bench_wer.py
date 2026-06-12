"""Offline WER benchmark over recorded dictations.

Usage: bench_wer.py refs.txt [audio_dir]
refs.txt: one reference sentence per line, in the ORDER they were dictated.
audio_dir: defaults to ~/Library/Application Support/whisperquiet/audio —
the N most recent WAVs are paired with the N reference lines by order.
Runs a variant grid and prints a WER table; this is the harness iteration
agents use to tune whispered-speech accuracy without burning user time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def wer(ref: str, hyp: str) -> float:
    import re
    norm = lambda t: re.sub(r"[^\w\s']", "", t.lower()).split()  # noqa: E731
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


VARIANTS = {
    "current (turbo+norm+gates)": {},
    "no gates": {"strip_gates": True},
    "large-v3 full": {"model": "mlx-community/whisper-large-v3-mlx"},
}


def main() -> int:
    import numpy as np
    import wave as wave_mod
    import mlx_whisper

    refs = [l.strip() for l in open(sys.argv[1]) if l.strip()]
    adir = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        Path.home() / "Library" / "Application Support" / "whisperquiet" / "audio"
    )
    wavs = sorted(adir.glob("*.wav"))[-len(refs):]
    if len(wavs) < len(refs):
        print(f"need {len(refs)} wavs, found {len(wavs)} in {adir}")
        return 1
    pairs = []
    for path in wavs:
        with wave_mod.open(str(path)) as w:
            audio = np.frombuffer(
                w.readframes(w.getnframes()), dtype=np.int16
            ).astype(np.float32) / 32767.0
        pairs.append(audio)

    from whisperquiet.audio import peak_normalize, trim_trailing_silence
    for name, opts in VARIANTS.items():
        model = opts.get("model", "mlx-community/whisper-large-v3-turbo")
        scores = []
        for ref, audio in zip(refs, pairs):
            a = peak_normalize(trim_trailing_silence(audio))
            kwargs = dict(path_or_hf_repo=model, language="en",
                          condition_on_previous_text=False)
            if not opts.get("strip_gates"):
                kwargs.update(temperature=0.0)
            out = mlx_whisper.transcribe(a, **kwargs)["text"].strip()
            scores.append(wer(ref, out))
        print(f"{name:32s} WER {100*sum(scores)/len(scores):5.1f}%  "
              f"worst {100*max(scores):5.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
