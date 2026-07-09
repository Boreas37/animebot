import asyncio
import logging
import os
import time

import asyncpg
from aiogram.types import FSInputFile

logger = logging.getLogger(__name__)

CRAWL_ALL_ANIME = False
POPULAR_QUEUE: list[tuple[str, int]] = []
CHECK_INTERVAL = 10
PRELOAD_TIMEOUT = 300


class Preloader:
    def __init__(self, pool: asyncpg.Pool, download_semaphore, cache_group_id_ref, tg_cache, bot_ref):
        self.pool = pool
        self.download_semaphore = download_semaphore
        self.cache_group_id_ref = cache_group_id_ref
        self.tg_cache = tg_cache
        self.bot_ref = bot_ref
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def init(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS preload_checklist (
                    id SERIAL PRIMARY KEY,
                    anime_slug TEXT NOT NULL,
                    episode_no INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at DOUBLE PRECISION,
                    UNIQUE(anime_slug, episode_no)
                )
            """)

    async def _conn_execute_seed(self, anime_slug: str, episode_no: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO preload_checklist (anime_slug, episode_no) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                anime_slug, episode_no,
            )

    async def _seed_queue(self) -> None:
        inserted = 0
        async with self.pool.acquire() as conn:
            existing = {
                f"{row['anime_slug']}:{row['episode_no']}"
                for row in await conn.fetch("SELECT anime_slug, episode_no FROM preload_checklist")
            }
            for slug, total in POPULAR_QUEUE:
                for ep in range(1, total + 1):
                    k = f"{slug}:{ep}"
                    if k not in existing:
                        await conn.execute(
                            "INSERT INTO preload_checklist (anime_slug, episode_no) VALUES ($1, $2) "
                            "ON CONFLICT DO NOTHING",
                            slug, ep,
                        )
                        inserted += 1

            if CRAWL_ALL_ANIME:
                import turkanime_api as ta
                from scraper import get_episodes
                full_list = ta.Anime.get_anime_listesi()
                for slug, _ in full_list:
                    try:
                        episodes = get_episodes(slug)
                    except Exception:
                        continue
                    for ep in episodes:
                        k = f"{slug}:{ep['no']}"
                        if k not in existing:
                            await conn.execute(
                                "INSERT INTO preload_checklist (anime_slug, episode_no) VALUES ($1, $2) "
                                "ON CONFLICT DO NOTHING",
                                slug, ep["no"],
                            )
                            inserted += 1
                logger.info(f"Seed: {len(full_list)} anime, {inserted} new episodes total")

        logger.info(f"Seeded {inserted} new episodes")

    async def _next_pending(self):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT id, anime_slug, episode_no FROM preload_checklist "
                "WHERE status = 'pending' ORDER BY id LIMIT 1"
            )

    async def _mark(self, row_id: int, status: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE preload_checklist SET status = $2, started_at = $3 WHERE id = $1",
                row_id, status, time.time(),
            )

    async def add_to_queue(self, anime_slug: str) -> str:
        from scraper import get_episodes
        try:
            episodes = get_episodes(anime_slug)
        except Exception as e:
            return f"Bölümler alınamadı: {e}"

        added = 0
        async with self.pool.acquire() as conn:
            existing = {
                f"{row['anime_slug']}:{row['episode_no']}"
                for row in await conn.fetch("SELECT anime_slug, episode_no FROM preload_checklist")
            }
            for ep in episodes:
                k = f"{anime_slug}:{ep['no']}"
                if k not in existing:
                    await conn.execute(
                        "INSERT INTO preload_checklist (anime_slug, episode_no) VALUES ($1, $2) "
                        "ON CONFLICT DO NOTHING",
                        anime_slug, ep["no"],
                    )
                    added += 1
        return f"{anime_slug}: {added} bölüm eklendi"

    async def stats(self) -> str:
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM preload_checklist")
            done = await conn.fetchval("SELECT COUNT(*) FROM preload_checklist WHERE status = 'done'")
            pending = await conn.fetchval("SELECT COUNT(*) FROM preload_checklist WHERE status = 'pending'")
        return f"Ön yükleme: {done}/{total} tamam, {pending} sırada"

    async def start(self) -> None:
        await self._seed_queue()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while not self._stopped:
            try:
                await self._tick()
            except Exception:
                logger.exception("Preloader tick failed")
            await asyncio.sleep(CHECK_INTERVAL)

    async def _tick(self) -> None:
        gid = self.cache_group_id_ref()
        if not gid:
            return

        row = await self._next_pending()
        if row is None:
            return

        row_id, anime_slug, episode_no = row["id"], row["anime_slug"], row["episode_no"]

        from cache_manager import CacheManager

        key = CacheManager.make_key(anime_slug, episode_no)
        if await self.tg_cache.exists(key):
            await self._mark(row_id, "done")
            return

        logger.info(f"Preloading {anime_slug} ep{episode_no}...")

        async with self.download_semaphore:
            from downloader import download_video
            from scraper import get_episodes, get_video_sources, resolve_video_url

            try:
                episodes = await asyncio.wait_for(
                    asyncio.to_thread(get_episodes, anime_slug),
                    timeout=120,
                )
            except (asyncio.TimeoutError, Exception):
                logger.warning(f"Preload: failed to get episodes for {anime_slug}")
                await self._mark(row_id, "failed")
                return

            ep = next((e for e in episodes if e["no"] == episode_no), None)
            if ep is None:
                await self._mark(row_id, "failed")
                return

            try:
                sources = await asyncio.wait_for(
                    asyncio.to_thread(get_video_sources, ep["slug"]),
                    timeout=120,
                )
            except (asyncio.TimeoutError, Exception):
                logger.warning(f"Preload: failed to get sources for {anime_slug} ep{episode_no}")
                await self._mark(row_id, "failed")
                return

            for src in sources:
                try:
                    url = await asyncio.wait_for(
                        asyncio.to_thread(resolve_video_url, src["video"]),
                        timeout=30,
                    )
                except (asyncio.TimeoutError, Exception):
                    continue
                if not url:
                    continue
                try:
                    tmp_path = await asyncio.wait_for(
                        download_video(url),
                        timeout=120,
                    )
                except Exception as e:
                    logger.warning(f"Preload download failed {anime_slug} ep{episode_no} {src['player']}: {e}")
                    continue

                try:
                    input_file = FSInputFile(tmp_path)
                    sent = await self.bot_ref().send_video(
                        chat_id=gid,
                        video=input_file,
                        caption=f"{anime_slug} Bölüm {episode_no}",
                    )
                    if sent.video:
                        await self.tg_cache.put(
                            key,
                            chat_id=gid,
                            message_id=sent.message_id,
                            file_id=sent.video.file_id,
                        )
                except Exception as e:
                    logger.warning(f"Preload send to group failed: {e}")
                    continue
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

                await self._mark(row_id, "done")
                logger.info(f"Preloaded {anime_slug} ep{episode_no}")
                return

            await self._mark(row_id, "failed")
