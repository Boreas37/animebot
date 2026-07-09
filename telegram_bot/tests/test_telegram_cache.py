import pytest

from telegram_cache import TelegramCache


@pytest.fixture
async def tg_cache(pool):
    c = TelegramCache(pool)
    await c.init()
    return c


async def test_put_then_get(tg_cache):
    await tg_cache.put("k1", chat_id=-100123, message_id=42, file_id="ABC")

    entry = await tg_cache.get("k1")

    assert entry == {"chat_id": -100123, "message_id": 42, "file_id": "ABC"}


async def test_get_missing_returns_none(tg_cache):
    assert await tg_cache.get("missing") is None


async def test_exists(tg_cache):
    assert await tg_cache.exists("k2") is False
    await tg_cache.put("k2", chat_id=-100123, message_id=1, file_id="X")
    assert await tg_cache.exists("k2") is True


async def test_count(tg_cache):
    assert await tg_cache.count() == 0
    await tg_cache.put("k3", chat_id=-100123, message_id=1, file_id="X")
    assert await tg_cache.count() == 1
