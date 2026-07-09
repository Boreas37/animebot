import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from preloader import Preloader


@pytest.fixture
async def preloader(pool):
    p = Preloader(
        pool=pool,
        download_semaphore=asyncio.Semaphore(1),
        cache_group_id_ref=lambda: -100123,
        tg_cache=MagicMock(exists=AsyncMock(return_value=False), put=AsyncMock()),
        bot_ref=lambda: MagicMock(send_video=AsyncMock(return_value=MagicMock(
            video=MagicMock(file_id="FILE1"), message_id=7,
        ))),
    )
    await p.init()
    return p


async def test_add_to_queue_and_stats(preloader, monkeypatch):
    monkeypatch.setattr(
        "scraper.get_episodes",
        lambda slug: [{"no": 1, "slug": "ep1"}, {"no": 2, "slug": "ep2"}],
    )

    result = await preloader.add_to_queue("naruto")

    assert "2" in result
    stats = await preloader.stats()
    assert "0/2" in stats


async def test_tick_removes_temp_file_after_send(preloader, monkeypatch, tmp_path):
    monkeypatch.setattr("scraper.get_episodes", lambda slug: [{"no": 1, "slug": "ep1"}])
    await preloader._conn_execute_seed("naruto", 1)

    tmp_file = tmp_path / "downloaded.mp4"
    tmp_file.write_bytes(b"x" * 10)

    monkeypatch.setattr("scraper.get_video_sources", lambda slug: [{"player": "p1", "video": object()}])
    monkeypatch.setattr("scraper.resolve_video_url", lambda v: "http://example.com/video")

    async def fake_download(url):
        return str(tmp_file)

    monkeypatch.setattr("downloader.download_video", fake_download)

    await preloader._tick()

    assert not tmp_file.exists()
