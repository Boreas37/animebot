import asyncio
import time

import pytest
from cachetools import TTLCache


async def test_ttl_cache_expires_entries():
    cache = TTLCache(maxsize=1000, ttl=0.05)
    cache[1] = "slug-a"
    assert cache.get(1) == "slug-a"
    await asyncio.sleep(0.1)
    assert cache.get(1) is None


async def test_release_download_lock_removes_unheld_lock():
    from bot import _download_locks, _get_download_lock, _release_download_lock

    lock = await _get_download_lock("test-key")
    assert "test-key" in _download_locks

    async with lock:
        pass

    await _release_download_lock("test-key")
    assert "test-key" not in _download_locks


async def test_release_download_lock_keeps_locked_lock():
    from bot import _download_locks, _get_download_lock, _release_download_lock

    lock = await _get_download_lock("test-key-2")
    async with lock:
        await _release_download_lock("test-key-2")
        assert "test-key-2" in _download_locks
