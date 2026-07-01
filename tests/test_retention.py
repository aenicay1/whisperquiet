import os
import time
from pathlib import Path

from whisperquiet.retention import MIB, prune_audio_dir


def _wav(path: Path, size: int, mtime: float) -> None:
    path.write_bytes(b"0" * size)
    os.utime(path, (mtime, mtime))


def test_prune_audio_deletes_files_older_than_retention_window(tmp_path):
    now = time.time()
    old = tmp_path / "old.wav"
    fresh = tmp_path / "fresh.wav"
    _wav(old, 10, now - 40 * 24 * 60 * 60)
    _wav(fresh, 10, now)

    result = prune_audio_dir(tmp_path, max_days=30, now=now)

    assert result.deleted_count == 1
    assert result.deleted_bytes == 10
    assert not old.exists()
    assert fresh.exists()


def test_prune_audio_keeps_directory_under_size_limit(tmp_path):
    now = time.time()
    oldest = tmp_path / "001.wav"
    middle = tmp_path / "002.wav"
    newest = tmp_path / "003.wav"
    _wav(oldest, MIB, now - 3)
    _wav(middle, MIB, now - 2)
    _wav(newest, MIB, now - 1)

    result = prune_audio_dir(tmp_path, max_mb=2, now=now)

    assert result.deleted_count == 1
    assert result.deleted_bytes == MIB
    assert result.remaining_bytes == 2 * MIB
    assert not oldest.exists()
    assert middle.exists()
    assert newest.exists()


def test_prune_audio_ignores_non_wav_files(tmp_path):
    now = time.time()
    note = tmp_path / "keep.txt"
    note.write_text("not audio")
    _wav(tmp_path / "old.wav", 4, now - 40 * 24 * 60 * 60)

    prune_audio_dir(tmp_path, max_days=30, now=now)

    assert note.exists()
