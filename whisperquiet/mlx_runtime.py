"""Best-effort MLX memory controls that never make dictation fail.

Whisper's model stays resident for low-latency dictation. MLX also retains a
large allocator cache after decode; that cache is safe to release without
unloading model weights. Import MLX lazily so headless tests and camera-only
code paths do not require a Metal device.
"""

from __future__ import annotations

from dataclasses import dataclass

MIB = 1024 * 1024


@dataclass(frozen=True)
class MemorySnapshot:
    active_mb: int
    cache_mb: int
    peak_mb: int


@dataclass(frozen=True)
class ReclaimResult:
    before: MemorySnapshot
    after: MemorySnapshot
    reclaimed_mb: int


def _metal_api():
    import mlx.core as mx

    if hasattr(mx, "get_active_memory"):
        return mx
    return mx.metal


def _mb(value: int) -> int:
    return int(round(value / MIB))


def snapshot(metal=None) -> MemorySnapshot | None:
    """Return allocator counters, or ``None`` when Metal is unavailable."""
    try:
        api = metal if metal is not None else _metal_api()
        return MemorySnapshot(
            active_mb=_mb(api.get_active_memory()),
            cache_mb=_mb(api.get_cache_memory()),
            peak_mb=_mb(api.get_peak_memory()),
        )
    except Exception:
        return None


def configure(
    cache_limit_mb: int = 0,
    memory_limit_mb: int = 0,
    *,
    metal=None,
) -> None:
    """Apply positive allocator limits; zero leaves the MLX default intact."""
    try:
        api = metal if metal is not None else _metal_api()
        if cache_limit_mb > 0:
            api.set_cache_limit(int(cache_limit_mb * MIB))
        if memory_limit_mb > 0:
            api.set_memory_limit(int(memory_limit_mb * MIB))
    except Exception:
        pass


def reclaim(metal=None) -> ReclaimResult | None:
    """Release cached buffers while preserving active model allocations."""
    try:
        api = metal if metal is not None else _metal_api()
        before = snapshot(api)
        if before is None:
            return None
        api.clear_cache()
        after = snapshot(api)
        if after is None:
            return None
        return ReclaimResult(
            before=before,
            after=after,
            reclaimed_mb=max(0, before.cache_mb - after.cache_mb),
        )
    except Exception:
        return None
