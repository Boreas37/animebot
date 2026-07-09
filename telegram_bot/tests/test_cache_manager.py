import asyncio
import os

import pytest

from cache_manager import CacheManager


@pytest.fixture
async def cache(pool, tmp_path):
    c = CacheManager(pool, cache_dir=str(tmp_path), max_bytes=1_000_000)
    await c.init()
    return c


async def test_put_then_get_returns_path(cache, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x" * 100)

    dest = await cache.put("k1", str(src), filename="k1.mp4")

    assert os.path.exists(dest)
    got = await cache.get("k1")
    assert got == dest


async def test_get_missing_key_returns_none(cache):
    assert await cache.get("nope") is None


async def test_get_prunes_row_when_file_deleted(cache, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x" * 100)
    dest = await cache.put("k2", str(src), filename="k2.mp4")
    os.remove(dest)

    assert await cache.get("k2") is None
    stats = await cache.stats()
    assert stats["entry_count"] == 0


async def test_evict_removes_file_and_row(cache, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x" * 100)
    dest = await cache.put("k3", str(src), filename="k3.mp4")

    await cache.evict("k3")

    assert not os.path.exists(dest)
    assert await cache.get("k3") is None


async def test_put_evicts_lowest_score_when_over_budget(cache, tmp_path):
    # max_bytes=1_000_000; each file is 600_000 bytes, so the 2nd put must evict the 1st.
    src1 = tmp_path / "a.mp4"
    src1.write_bytes(b"x" * 600_000)
    src2 = tmp_path / "b.mp4"
    src2.write_bytes(b"y" * 600_000)

    dest1 = await cache.put("a", str(src1), filename="a.mp4")
    await cache.put("b", str(src2), filename="b.mp4")

    assert await cache.get("a") is None
    assert not os.path.exists(dest1)


async def test_config_roundtrip(cache):
    assert await cache.get_config("cache_group_id") is None
    await cache.set_config("cache_group_id", "-100123")
    assert await cache.get_config("cache_group_id") == "-100123"


async def test_concurrent_puts_do_not_exceed_budget(cache, tmp_path):
    # max_bytes=1_000_000; two concurrent 700_000-byte puts must not both survive
    # uncontested — that would leave usage at 1_400_000, over budget with nothing
    # left to trigger a correction. Regression test for the non-atomic budget race.
    src1 = tmp_path / "c1.mp4"
    src1.write_bytes(b"x" * 700_000)
    src2 = tmp_path / "c2.mp4"
    src2.write_bytes(b"y" * 700_000)

    await asyncio.gather(
        cache.put("c1", str(src1), filename="c1.mp4"),
        cache.put("c2", str(src2), filename="c2.mp4"),
    )

    stats = await cache.stats()
    assert stats["usage_bytes"] <= cache.max_bytes
