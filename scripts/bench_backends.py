"""Backend A/B: mlx-whisper turbo vs NVIDIA Parakeet TDT, on this app + hardware.

Runs the SAME recorded dictation takes through both dictation backends — the
shipping whisper backend (whisperquiet/transcribe.py) and the opt-in parakeet
backend (whisperquiet/parakeet.py) — and reports, per backend:

    * WER          accuracy vs the reference read (same scorer as bench_wer.py)
    * latency/take wall-clock seconds to decode one full take (steady state)
    * RTF          real-time factor = decode_s / audio_s (lower = faster)
    * peak memory  MLX active-memory peak AND process RSS peak

The point is to make the turbo-vs-parakeet swap a data-driven decision for THIS
app and THIS Mac, not a quote from a blog post. It does NOT change the default
backend — config.dictation_backend stays "whisper". This is measurement only.

Each backend runs in its OWN subprocess so peak-memory numbers are clean: the
two models are large and MLX peak memory is process-global, so measuring them in
one process would let the first model's weights pollute the second's peak. The
subprocess also makes the optional dependency graceful — if parakeet-mlx is not
installed, that leg reports "not installed" instead of crashing the run.

Usage:
    bench_backends.py refs.txt [audio_dir] [--takes N] [--only whisper|parakeet]
                       [--whisper-model REPO] [--parakeet-model REPO]
                       [--language en] [--show-text]

refs.txt: reference sentences, one per line; each WAV take is a full read of all
lines, scored against their concatenation (identical contract to bench_wer.py).
audio_dir defaults to the app's on-device audio store; the N most recent WAVs
(default 2) are the takes.

Install the parakeet leg with:  pip install -e '.[parakeet]'
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_SCRIPT_DIR))

from bench_wer import load_refs, wer  # noqa: E402  reuse scorer + ref loader

WHISPER = "whisper"
PARAKEET = "parakeet"
# A worker prints exactly one line beginning with this marker; everything else
# on stdout (model download bars, mlx_whisper's own logging) is ignored.
MARKER = "__BENCH_JSON__"
DEFAULT_WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


# --------------------------------------------------------------------------- #
# pure helpers (unit-tested; no model, no subprocess)
# --------------------------------------------------------------------------- #
def human_bytes(n: float | None) -> str:
    """Bytes -> a compact human string. ``None`` (not measured) -> '—'."""
    if n is None:
        return "—"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def rtf(decode_s: float, audio_s: float) -> float:
    """Real-time factor: decode seconds per second of audio. 0 if no audio."""
    return decode_s / audio_s if audio_s > 0 else 0.0


def worker_cmd(python: str, backend: str, model: str, language: str,
               wavs: list[str]) -> list[str]:
    """Build the argv for a per-backend worker subprocess."""
    return [python, str(Path(__file__).resolve()), "--_worker", backend,
            "--model", model, "--language", language, *wavs]


def extract_json(stdout: str) -> dict:
    """Pull the marker-tagged JSON line out of a worker's stdout."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER):])
    raise ValueError("no benchmark JSON found in worker output")


def summarize(result: dict, full_ref: str) -> dict:
    """Fold a worker result into per-take WER and backend-level means.

    Returns {takes:[{wav,audio_s,decode_s,rtf,wer,text}], mean_wer, mean_rtf,
    mean_latency, peak_mlx_bytes, peak_rss_bytes, load_s}. WER uses the shared
    scorer so these numbers line up with bench_wer.py.
    """
    takes = []
    for t in result.get("takes", []):
        takes.append({
            "wav": t["wav"],
            "audio_s": t["audio_s"],
            "decode_s": t["decode_s"],
            "rtf": rtf(t["decode_s"], t["audio_s"]),
            "wer": 100 * wer(full_ref, t["text"]),
            "text": t["text"],
        })
    n = len(takes) or 1
    return {
        "takes": takes,
        "mean_wer": sum(t["wer"] for t in takes) / n,
        "mean_rtf": sum(t["rtf"] for t in takes) / n,
        "mean_latency": sum(t["decode_s"] for t in takes) / n,
        "peak_mlx_bytes": result.get("peak_mlx_bytes"),
        "peak_rss_bytes": result.get("peak_rss_bytes"),
        "load_s": result.get("load_s"),
    }


# --------------------------------------------------------------------------- #
# worker: load one backend, transcribe every take, report timings + peak memory
# --------------------------------------------------------------------------- #
def run_worker(backend: str, model: str, language: str, wavs: list[str]) -> int:
    import numpy as np
    import wave as wave_mod

    import mlx.core as mx

    from whisperquiet.audio import SAMPLE_RATE

    if backend == PARAKEET:
        from whisperquiet import parakeet as impl
    else:
        from whisperquiet import transcribe as impl

    def load_audio(path: str) -> np.ndarray:
        with wave_mod.open(path) as w:
            raw = w.readframes(w.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0

    def emit(payload: dict) -> int:
        print(MARKER + json.dumps(payload), flush=True)
        return 0

    # Reset BEFORE loading so the peak captures the model weights, not just the
    # decode scratch. ru_maxrss is process-lifetime peak (bytes on macOS).
    mx.reset_peak_memory()
    try:
        # Warm-up decode (1s of silence, above the backend's min-audio guard) so
        # model load / first-call compile is timed as load_s, not folded into a
        # take's decode time. For parakeet this is where a missing dependency
        # surfaces — caught below and reported as "not installed".
        warm = np.zeros(SAMPLE_RATE, dtype=np.float32)
        t0 = time.perf_counter()
        impl.transcribe(warm, model, language=language)
        load_s = time.perf_counter() - t0
    except ImportError as exc:
        return emit({"backend": backend, "unavailable":
                     f"{exc} — install with: pip install -e '.[parakeet]'"})
    except Exception as exc:  # model download/load failure, OOM, etc.
        return emit({"backend": backend, "error": f"{type(exc).__name__}: {exc}"})

    takes = []
    for path in wavs:
        audio = load_audio(path)
        audio_s = audio.size / SAMPLE_RATE
        t0 = time.perf_counter()
        # transcribe_long is the committed-text path the app actually runs on
        # release (whisper splits on silence; parakeet chunks natively).
        text = impl.transcribe_long(audio, model, language=language)
        takes.append({"wav": Path(path).name, "audio_s": audio_s,
                      "decode_s": time.perf_counter() - t0, "text": text})

    import resource
    return emit({
        "backend": backend,
        "model": model,
        "load_s": load_s,
        "peak_mlx_bytes": int(mx.get_peak_memory()),
        "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "takes": takes,
    })


# --------------------------------------------------------------------------- #
# parent: spawn a worker per backend, score, print the comparison
# --------------------------------------------------------------------------- #
def run_backend(backend: str, model: str, language: str,
                wavs: list[str]) -> dict:
    """Run one backend in a subprocess; return its parsed result or an error."""
    cmd = worker_cmd(sys.executable, backend, model, language, wavs)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return extract_json(proc.stdout)
    except ValueError:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return {"backend": backend,
                "error": "worker produced no result:\n  " + "\n  ".join(tail)}


def main() -> int:
    # worker dispatch (internal; not part of the public CLI surface)
    if "--_worker" in sys.argv:
        wp = argparse.ArgumentParser()
        wp.add_argument("--_worker", required=True)
        wp.add_argument("--model", required=True)
        wp.add_argument("--language", default="en")
        wp.add_argument("wavs", nargs="*")
        wa = wp.parse_args()
        return run_worker(wa._worker, wa.model, wa.language, wa.wavs)

    ap = argparse.ArgumentParser()
    ap.add_argument("refs")
    ap.add_argument("audio_dir", nargs="?", default=str(
        Path.home() / "Library" / "Application Support" / "whisperquiet" / "audio"))
    ap.add_argument("--takes", type=int, default=2,
                    help="N most recent WAVs; each is a full read of refs")
    ap.add_argument("--only", choices=[WHISPER, PARAKEET], default="",
                    help="run only this backend")
    ap.add_argument("--whisper-model", default=DEFAULT_WHISPER_MODEL)
    ap.add_argument("--parakeet-model", default=DEFAULT_PARAKEET_MODEL)
    ap.add_argument("--language", default="en")
    ap.add_argument("--show-text", action="store_true",
                    help="print each transcription")
    args = ap.parse_args()

    refs = load_refs(args.refs)
    full_ref = " ".join(refs)
    wavs = sorted(Path(args.audio_dir).glob("*.wav"),
                  key=lambda p: p.stat().st_mtime)[-args.takes:]
    if len(wavs) < args.takes:
        print(f"need {args.takes} wavs, found {len(wavs)} in {args.audio_dir}")
        return 1
    wav_paths = [str(p) for p in wavs]

    plan = [(WHISPER, args.whisper_model), (PARAKEET, args.parakeet_model)]
    if args.only:
        plan = [(b, m) for b, m in plan if b == args.only]

    print(f"takes: {len(wav_paths)} most recent in {args.audio_dir}")
    print(f"refs:  {len(refs)} lines, {len(full_ref.split())} words\n")

    rows = []
    for backend, model in plan:
        print(f"running {backend} ({model}) …", flush=True)
        t0 = time.perf_counter()
        result = run_backend(backend, model, args.language, wav_paths)
        wall = time.perf_counter() - t0
        if "unavailable" in result:
            rows.append((backend, model, None, f"not installed — {result['unavailable']}"))
        elif "error" in result:
            rows.append((backend, model, None, f"error — {result['error']}"))
        else:
            rows.append((backend, model, summarize(result, full_ref), None))
            print(f"  done in {wall:.0f}s "
                  f"(model load {result.get('load_s', 0):.1f}s)", flush=True)

    # comparison table
    print()
    head = (f"{'backend':<9}{'model':<28}{'WER':>8}{'lat/take':>11}"
            f"{'RTF':>9}{'peak MLX':>12}{'peak RSS':>12}")
    print(head)
    print("-" * len(head))
    for backend, model, summary, note in rows:
        short = model.split("/")[-1][:26]
        if summary is None:
            print(f"{backend:<9}{short:<28}  {note}")
            continue
        print(f"{backend:<9}{short:<28}"
              f"{summary['mean_wer']:7.1f}%"
              f"{summary['mean_latency']:10.2f}s"
              f"{summary['mean_rtf']:9.3f}"
              f"{human_bytes(summary['peak_mlx_bytes']):>12}"
              f"{human_bytes(summary['peak_rss_bytes']):>12}")

    if args.show_text:
        print()
        for backend, model, summary, note in rows:
            if summary is None:
                continue
            for t in summary["takes"]:
                print(f"[{backend}/{t['wav']}] ({t['wer']:.1f}% WER) {t['text']}")

    print("\nNote: bench_backends measures the real app backend path "
          "(transcribe_long, with each backend's own pre-processing). "
          "Record the verdict in docs/BACKLOG.md before flipping "
          "config.dictation_backend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
