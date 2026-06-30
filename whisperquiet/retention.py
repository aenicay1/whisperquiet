"""Best-effort pruning for on-device dogfood artifacts."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024


@dataclass(frozen=True)
class PruneResult:
    deleted_count: int = 0
    deleted_bytes: int = 0
    remaining_bytes: int = 0


@dataclass(frozen=True)
class _AudioFile:
    path: Path
    size: int
    mtime: float


def prune_audio_dir(
    audio_dir: Path,
    *,
    max_mb: int = 0,
    max_days: int = 0,
    now: float | None = None,
) -> PruneResult:
    """Delete old/extra WAV files, oldest first, without touching other files.

    A zero or negative limit disables that limit. IO races are ignored because
    this runs beside the live app and should never break dictation.
    """
    now = time.time() if now is None else now
    files = _list_audio(audio_dir)
    deleted_count = 0
    deleted_bytes = 0

    if max_days > 0:
        cutoff = now - (max_days * 24 * 60 * 60)
        expired = [f for f in files if f.mtime < cutoff]
        for file in expired:
            if _delete(file.path):
                deleted_count += 1
                deleted_bytes += file.size
        files = [f for f in files if f.mtime >= cutoff and f.path.exists()]

    if max_mb > 0:
        limit = max_mb * MIB
        total = sum(f.size for f in files)
        for file in sorted(files, key=lambda f: (f.mtime, f.path.name)):
            if total <= limit:
                break
            if _delete(file.path):
                total -= file.size
                deleted_count += 1
                deleted_bytes += file.size

    return PruneResult(
        deleted_count=deleted_count,
        deleted_bytes=deleted_bytes,
        remaining_bytes=sum(f.size for f in _list_audio(audio_dir)),
    )


def _list_audio(audio_dir: Path) -> list[_AudioFile]:
    try:
        paths = list(Path(audio_dir).glob("*.wav"))
    except OSError:
        return []
    files: list[_AudioFile] = []
    for path in paths:
        try:
            stat = path.stat()
            if path.is_file():
                files.append(_AudioFile(path=path, size=stat.st_size, mtime=stat.st_mtime))
        except OSError:
            continue
    return files


def _delete(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except OSError:
        return False
