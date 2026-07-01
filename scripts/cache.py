#!/usr/bin/env python3
"""Inspect local WhisperQuiet app data and Hugging Face model caches."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from whisperquiet.config import CONFIG_DIR, Config
from whisperquiet.retention import MIB, prune_audio_dir


def _size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _fmt(size: int) -> str:
    if size >= 1024 * MIB:
        return f"{size / (1024 * MIB):.1f} GB"
    if size >= MIB:
        return f"{size / MIB:.0f} MB"
    return f"{size / 1024:.0f} KB"


def _hf_home() -> Path:
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))


def _repo_from_cache_dir(name: str) -> str:
    if name.startswith("models--"):
        return name.removeprefix("models--").replace("--", "/")
    return name


def _model_dirs(hf_home: Path) -> list[Path]:
    hub = hf_home / "hub"
    try:
        return sorted(
            [p for p in hub.iterdir() if p.is_dir() and p.name.startswith("models--")],
            key=lambda p: p.name,
        )
    except OSError:
        return []


def render_status(
    *,
    config_dir: Path = CONFIG_DIR,
    hf_home: Path | None = None,
    config: Config | None = None,
) -> str:
    hf_home = _hf_home() if hf_home is None else hf_home
    config = Config() if config is None else config
    audio_dir = config_dir / "audio"
    app_paths = [
        ("audio", audio_dir),
        ("stats", config_dir / "stats.jsonl"),
        ("feedback", config_dir / "feedback.jsonl"),
        ("transcripts", config_dir / "transcripts.jsonl"),
        ("config", config_dir / "config.json"),
    ]

    lines = ["whisperquiet cache status", "", f"app data: {config_dir}"]
    for label, path in app_paths:
        lines.append(f"  {label:<11} {_fmt(_size(path)):>8}  {path}")
    lines += ["", f"huggingface cache: {hf_home / 'hub'}"]

    model_dirs = _model_dirs(hf_home)
    if not model_dirs:
        lines.append("  no model caches found")
        return "\n".join(lines)

    shipping = {config.model_repo}
    optional = {config.parakeet_repo}
    total = 0
    for path in sorted(model_dirs, key=_size, reverse=True):
        size = _size(path)
        total += size
        repo = _repo_from_cache_dir(path.name)
        marker = "shipping" if repo in shipping else "optional" if repo in optional else "extra"
        lines.append(f"  {_fmt(size):>8}  {marker:<8}  {repo}")
    lines.append(f"  {_fmt(total):>8}  total")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status", help="show app-data and model-cache sizes")
    prune = sub.add_parser("prune-audio", help="apply configured audio retention now")
    prune.add_argument("--max-mb", type=int, default=Config().audio_retention_mb)
    prune.add_argument("--max-days", type=int, default=Config().audio_retention_days)
    args = parser.parse_args()

    if args.cmd in (None, "status"):
        print(render_status())
        return
    if args.cmd == "prune-audio":
        result = prune_audio_dir(
            CONFIG_DIR / "audio",
            max_mb=args.max_mb,
            max_days=args.max_days,
        )
        print(
            "audio pruned:"
            f" deleted={result.deleted_count}"
            f" freed={_fmt(result.deleted_bytes)}"
            f" remaining={_fmt(result.remaining_bytes)}"
        )


if __name__ == "__main__":
    main()
