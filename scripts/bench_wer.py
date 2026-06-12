"""Offline WER grid benchmark over recorded dictation takes.

Usage: bench_wer.py refs.txt [audio_dir] [--takes N] [--only SUBSTR]
refs.txt: the reference sentences, one per line. Each WAV take is the user
reading ALL lines in order, so every take is scored against the full
concatenation of the reference lines.
audio_dir: defaults to ~/Library/Application Support/whisperquiet/audio —
the N most recent WAVs (default 2) are the takes.

Runs a variant grid (model x vocabulary prompt x decode gates x peak
normalization) and prints WER per take, mean WER, and rough per-take
transcription seconds. Transcriptions are cached in memory per
(wav, effective-config) so variants that share a cell are free. This is the
harness iteration agents use to tune whispered-speech accuracy without
burning user time.
"""
import argparse
import json
import sys
import time
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


TURBO = "mlx-community/whisper-large-v3-turbo"
FULL = "mlx-community/whisper-large-v3-mlx"

VOCAB = ("Springdale, Circleback, EBITDA, data room, letter of intent, "
         "working capital, due diligence, purchase agreement, quarterly report")

# the decode gates currently shipped in whisperquiet/transcribe.py
GATES_CURRENT = {
    "temperature": 0.0,
    "compression_ratio_threshold": 2.2,
    "logprob_threshold": -1.0,
    "no_speech_threshold": 0.5,
    "hallucination_silence_threshold": 2.0,
}

# variant -> config. Config keys:
#   model: HF repo (default turbo)
#   gates: dict of decode-gate kwargs (default GATES_CURRENT; {} = minimal,
#          i.e. only condition_on_previous_text=False)
#   prompt: initial_prompt string or None (default None)
#   normalize: peak_normalize on/off (default True)
VARIANTS = {
    "turbo gates norm        ": {},
    "turbo gates norm +vocab ": {"prompt": VOCAB},
    "turbo minimal norm      ": {"gates": {}},
    "turbo minimal norm +vocab": {"gates": {}, "prompt": VOCAB},
    "turbo gates RAW         ": {"normalize": False},
    "turbo gates RAW +vocab  ": {"normalize": False, "prompt": VOCAB},
    "full  gates norm        ": {"model": FULL},
    "full  gates norm +vocab ": {"model": FULL, "prompt": VOCAB},
    # refinement round: richer prompt phrasings around the turbo+vocab winner
    "refine sentence-style   ": {"prompt": (
        "Deal notes for the Springdale team: Circleback call notes, EBITDA "
        "adjustments, the data room, the letter of intent, working capital, "
        "due diligence, the purchase agreement, and the quarterly report.")},
    "refine staccato-style   ": {"prompt": (
        "M&A diligence dictation. Springdale. Circleback. EBITDA. Data room. "
        "Letter of intent. Working capital. Due diligence. Purchase "
        "agreement. Quarterly report.")},
    "refine vocab RAW minimal": {"gates": {}, "normalize": False,
                                 "prompt": VOCAB},
    "refine staccato minimal ": {"gates": {}, "prompt": (
        "M&A diligence dictation. Springdale. Circleback. EBITDA. Data room. "
        "Letter of intent. Working capital. Due diligence. Purchase "
        "agreement. Quarterly report.")},
}

_CACHE: dict = {}  # (wav_name, config_json) -> (text, seconds)


def run_variant(cfg: dict, takes: list) -> list:
    """Returns [(wer_pct, seconds, text), ...] per take."""
    import mlx_whisper
    from whisperquiet.audio import peak_normalize

    model = cfg.get("model", TURBO)
    kwargs = dict(path_or_hf_repo=model, language="en",
                  condition_on_previous_text=False, verbose=None)
    kwargs.update(cfg.get("gates", GATES_CURRENT))
    if cfg.get("prompt"):
        kwargs["initial_prompt"] = cfg["prompt"]
    normalize = cfg.get("normalize", True)
    key_cfg = json.dumps({**kwargs, "normalize": normalize}, sort_keys=True)

    out = []
    for name, ref, audio in takes:
        key = (name, key_cfg)
        if key not in _CACHE:
            a = peak_normalize(audio) if normalize else audio
            t0 = time.perf_counter()
            text = mlx_whisper.transcribe(a, **kwargs)["text"].strip()
            _CACHE[key] = (text, time.perf_counter() - t0)
        text, secs = _CACHE[key]
        out.append((100 * wer(ref, text), secs, text))
    return out


def main() -> int:
    import numpy as np
    import wave as wave_mod

    ap = argparse.ArgumentParser()
    ap.add_argument("refs")
    ap.add_argument("audio_dir", nargs="?", default=str(
        Path.home() / "Library" / "Application Support" / "whisperquiet" / "audio"))
    ap.add_argument("--takes", type=int, default=2,
                    help="N most recent WAVs; each is a full read of refs")
    ap.add_argument("--only", default="",
                    help="run only variants whose name contains this substring")
    ap.add_argument("--show-text", action="store_true",
                    help="print each transcription")
    args = ap.parse_args()

    refs = [l.strip() for l in open(args.refs) if l.strip()]
    full_ref = " ".join(refs)
    wavs = sorted(Path(args.audio_dir).glob("*.wav"),
                  key=lambda p: p.stat().st_mtime)[-args.takes:]
    if len(wavs) < args.takes:
        print(f"need {args.takes} wavs, found {len(wavs)} in {args.audio_dir}")
        return 1

    from whisperquiet.audio import trim_trailing_silence
    takes = []
    for path in wavs:
        with wave_mod.open(str(path)) as w:
            audio = np.frombuffer(
                w.readframes(w.getnframes()), dtype=np.int16
            ).astype(np.float32) / 32767.0
        # trailing-silence trim is unconditional in the app pipeline
        takes.append((path.name, full_ref, trim_trailing_silence(audio)))
    print("takes: " + ", ".join(f"{n} ({len(a)/16000:.0f}s)"
                                for n, _, a in takes))

    header = "variant".ljust(26) + "".join(
        f"  {n[:12]:>14s}" for n, _, _ in takes) + "   mean    sec/take"
    print(header)
    for name, cfg in VARIANTS.items():
        if args.only and args.only not in name:
            continue
        res = run_variant(cfg, takes)
        mean = sum(r[0] for r in res) / len(res)
        secs = sum(r[1] for r in res) / len(res)
        print(name.ljust(26) + "".join(f"  {r[0]:13.1f}%" for r in res)
              + f"  {mean:5.1f}%  {secs:7.1f}s")
        if args.show_text:
            for (tn, _, _), r in zip(takes, res):
                print(f"    [{tn}] {r[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
