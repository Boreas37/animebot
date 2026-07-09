import os
from unittest.mock import AsyncMock, MagicMock

import pytest


async def test_cmd_ara_does_not_create_orphan_cache_entry(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "search_anime", lambda q: [{"slug": "naruto", "title": "Naruto"}])
    bot._anime_cache.clear()
    bot._next_id = 0

    message = MagicMock()
    message.answer = AsyncMock(return_value=MagicMock(edit_text=AsyncMock()))
    command = MagicMock(args="naruto")

    await bot.cmd_ara(message, command)

    assert len(bot._anime_cache) == 1


async def test_setcache_rejects_non_admin(monkeypatch):
    import bot

    monkeypatch.setattr(bot.config, "ADMIN_IDS", {999})

    message = MagicMock()
    message.chat.type = "group"
    message.chat.title = "Test Group"
    message.chat.id = -100999
    message.from_user.id = 111
    message.answer = AsyncMock()

    await bot.cmd_setcache(message)

    message.answer.assert_awaited_once()
    assert "yetki" in message.answer.await_args.args[0].lower()


async def test_setcache_allows_admin(monkeypatch):
    import bot

    monkeypatch.setattr(bot.config, "ADMIN_IDS", {111})
    monkeypatch.setattr(bot, "cache", MagicMock(set_config=AsyncMock()))

    message = MagicMock()
    message.chat.type = "group"
    message.chat.title = "Test Group"
    message.chat.id = -100999
    message.from_user.id = 111
    message.answer = AsyncMock()

    await bot.cmd_setcache(message)

    bot.cache.set_config.assert_awaited_once_with("cache_group_id", "-100999")


async def test_do_download_and_send_reports_error_instead_of_hanging(monkeypatch):
    import bot

    monkeypatch.setattr(
        "bot.get_video_sources", lambda slug: [{"player": "p1", "video": object()}]
    )
    monkeypatch.setattr("bot.resolve_video_url", lambda v: "http://example.com/video.mp4")
    monkeypatch.setattr("bot.download_video", AsyncMock(return_value="/tmp/does-not-matter.mp4"))

    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(bot, "cache", MagicMock(put=boom))
    bot.cache_group_id = None

    status_msg = MagicMock(edit_text=AsyncMock())
    orig_msg = MagicMock()

    result = await bot._do_download_and_send(status_msg, orig_msg, "naruto", 1, "ep-slug", "naruto__1__default")

    assert result is False
    last_call_text = status_msg.edit_text.await_args.args[0]
    assert "hata" in last_call_text.lower() or "indirilemedi" in last_call_text.lower()


async def test_do_download_and_send_evicts_local_copy_after_group_upload(monkeypatch, tmp_path):
    import bot

    local_file = tmp_path / "naruto__1__default.mp4"
    local_file.write_bytes(b"x" * 10)

    monkeypatch.setattr("bot.get_video_sources", lambda slug: [{"player": "p1", "video": object()}])
    monkeypatch.setattr("bot.resolve_video_url", lambda v: "http://example.com/video.mp4")
    monkeypatch.setattr("bot.download_video", AsyncMock(return_value=str(local_file)))

    fake_cache = MagicMock(
        put=AsyncMock(return_value=str(local_file)),
        evict=AsyncMock(),
    )
    monkeypatch.setattr(bot, "cache", fake_cache)
    monkeypatch.setattr(bot, "tg_cache", MagicMock(put=AsyncMock()))
    bot.cache_group_id = -100123
    monkeypatch.setattr(bot, "bot", MagicMock(send_video=AsyncMock(
        return_value=MagicMock(video=MagicMock(file_id="F1"), message_id=9)
    )))
    monkeypatch.setattr(bot, "_send_video", AsyncMock())

    result = await bot._do_download_and_send(
        MagicMock(edit_text=AsyncMock()), MagicMock(), "naruto", 1, "ep-slug", "naruto__1__default"
    )

    assert result is True
    fake_cache.evict.assert_awaited_once_with("naruto__1__default")
