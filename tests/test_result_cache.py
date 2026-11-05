from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from constella import result_cache
from constella.result_cache import CacheBusyError, ResultCache


def test_fixed_ttl_starts_after_computation_and_hits_do_not_extend_it(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(result_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def run():
        cache = ResultCache()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            if calls == 1:
                clock[0] = 20
            return {"generation": calls}

        first = await cache.get("overview", load)
        clock[0] = 79.9
        assert await cache.get("overview", load) is first
        clock[0] = 80
        assert json.loads(await cache.get("overview", load)) == {"generation": 2}
        assert calls == 2

    asyncio.run(run())


def test_singleflight_survives_cancelled_waiter_and_bounds_distinct_misses():
    async def run():
        cache = ResultCache(max_pending=1)
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            entered.set()
            await finish.wait()
            return {"ok": True}

        first = asyncio.create_task(cache.get("shared", load))
        await entered.wait()
        second = asyncio.create_task(cache.get("shared", load))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(CacheBusyError):
            await cache.get("another", load)
        finish.set()
        assert json.loads(await second) == {"ok": True}
        assert calls == 1 and not cache.pending

    asyncio.run(run())


def test_failures_are_not_cached_and_same_key_can_retry():
    async def run():
        cache = ResultCache()

        async def fail():
            raise ValueError("failed query")

        with pytest.raises(ValueError, match="failed query"):
            await cache.get("key", fail)
        assert cache.size == 0 and not cache.entries and not cache.pending

        async def load():
            return {"ok": True}

        assert json.loads(await cache.get("key", load)) == {"ok": True}

    asyncio.run(run())


def test_byte_budget_lru_oversized_results_and_expired_cleanup(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(result_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def run():
        cache = ResultCache(max_bytes=8)

        async def small():
            return "ab"  # Four serialized bytes.

        await cache.get("a", small)
        await cache.get("b", small)
        await cache.get("a", small)
        await cache.get("c", small)
        assert list(cache.entries) == ["a", "c"] and cache.size == 8

        async def large():
            return "too large"

        assert json.loads(await cache.get("large", large)) == "too large"
        assert "large" not in cache.entries and cache.size == 8
        clock[0] = 60
        await cache.get("d", small)
        assert list(cache.entries) == ["d"] and cache.size == 4

    asyncio.run(run())


def test_entry_limit_bounds_small_results():
    async def run():
        cache = ResultCache(max_entries=2)

        async def load():
            return None

        for key in range(3):
            await cache.get(key, load)
        assert list(cache.entries) == [1, 2]

    asyncio.run(run())
