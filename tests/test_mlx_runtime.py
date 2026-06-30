from whisperquiet.mlx_runtime import configure, reclaim, snapshot


class FakeMetal:
    def __init__(self, *, active=1500, cache=2800, peak=4500):
        self.active = active * 1024 * 1024
        self.cache = cache * 1024 * 1024
        self.peak = peak * 1024 * 1024
        self.cache_limit = None
        self.memory_limit = None
        self.clear_calls = 0

    def get_active_memory(self):
        return self.active

    def get_cache_memory(self):
        return self.cache

    def get_peak_memory(self):
        return self.peak

    def set_cache_limit(self, value):
        self.cache_limit = value

    def set_memory_limit(self, value):
        self.memory_limit = value

    def clear_cache(self):
        self.clear_calls += 1
        self.cache = 96 * 1024 * 1024


def test_snapshot_reports_megabytes():
    snap = snapshot(FakeMetal())
    assert snap is not None
    assert snap.active_mb == 1500
    assert snap.cache_mb == 2800
    assert snap.peak_mb == 4500


def test_configure_applies_positive_limits():
    metal = FakeMetal()
    configure(cache_limit_mb=256, memory_limit_mb=4096, metal=metal)
    assert metal.cache_limit == 256 * 1024 * 1024
    assert metal.memory_limit == 4096 * 1024 * 1024


def test_configure_ignores_disabled_limits():
    metal = FakeMetal()
    configure(cache_limit_mb=0, memory_limit_mb=0, metal=metal)
    assert metal.cache_limit is None
    assert metal.memory_limit is None


def test_reclaim_clears_only_cached_allocations():
    metal = FakeMetal()
    result = reclaim(metal)
    assert result is not None
    assert metal.clear_calls == 1
    assert result.before.active_mb == 1500
    assert result.after.active_mb == 1500
    assert result.before.cache_mb == 2800
    assert result.after.cache_mb == 96
    assert result.reclaimed_mb == 2704


class BrokenMetal:
    def get_active_memory(self):
        raise RuntimeError("no Metal device")


def test_memory_telemetry_never_breaks_dictation():
    assert snapshot(BrokenMetal()) is None
    assert reclaim(BrokenMetal()) is None
