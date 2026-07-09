import os
import shutil
import time
from pathlib import Path

import asyncpg


class CacheManager:
    def __init__(self, pool: asyncpg.Pool, cache_dir: str, max_bytes: int):
        self.pool = pool
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    async def init(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    key TEXT PRIMARY KEY,
                    filepath TEXT NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    created_at DOUBLE PRECISION NOT NULL,
                    last_access DOUBLE PRECISION NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    @staticmethod
    def make_key(anime_slug: str, bolum_no: int, quality: str = "default") -> str:
        return f"{anime_slug}__{bolum_no}__{quality}"

    async def get(self, key: str) -> str | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT filepath FROM entries WHERE key = $1", key
            )
            if row is None:
                return None

            filepath = row["filepath"]
            if not os.path.exists(filepath):
                await conn.execute("DELETE FROM entries WHERE key = $1", key)
                return None

            await conn.execute(
                "UPDATE entries SET hit_count = hit_count + 1, last_access = $2 WHERE key = $1",
                key, time.time(),
            )
            return filepath

    async def put(self, key: str, source_path: str, filename: str) -> str:
        size_bytes = os.path.getsize(source_path)
        dest_path = self.cache_dir / filename
        shutil.move(source_path, str(dest_path))
        now = time.time()

        # The budget check (read usage) + eviction + insert must be one atomic unit,
        # or two concurrent put()s can both read "usage is fine" before either commits,
        # letting the cache grow past max_bytes with no future trigger to correct it.
        # A transaction-scoped advisory lock serializes this critical section across
        # every concurrent put() without needing row-level locks on `entries`.
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext('cache_manager_budget'))")
                await self._ensure_space_locked(conn, size_bytes)
                await conn.execute(
                    """INSERT INTO entries (key, filepath, size_bytes, hit_count, created_at, last_access)
                       VALUES ($1, $2, $3, 1, $4, $4)
                       ON CONFLICT (key) DO UPDATE SET
                         filepath = EXCLUDED.filepath,
                         size_bytes = EXCLUDED.size_bytes,
                         hit_count = 1,
                         created_at = EXCLUDED.created_at,
                         last_access = EXCLUDED.last_access""",
                    key, str(dest_path), size_bytes, now,
                )
        return str(dest_path)

    async def evict(self, key: str) -> None:
        """Remove a cache entry (file + row) immediately, without waiting for LRU/LFU eviction.

        Used after a successful upload to the Telegram cache group so the local
        copy doesn't linger waiting for eviction pressure.
        """
        async with self.pool.acquire() as conn:
            await self._evict_locked(conn, key)

    async def _evict_locked(self, conn: asyncpg.Connection, key: str) -> None:
        row = await conn.fetchrow("SELECT filepath FROM entries WHERE key = $1", key)
        if row is None:
            return
        filepath = row["filepath"]
        if os.path.exists(filepath):
            os.remove(filepath)
        await conn.execute("DELETE FROM entries WHERE key = $1", key)

    async def _current_usage(self) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COALESCE(SUM(size_bytes), 0) FROM entries")

    async def _ensure_space_locked(self, conn: asyncpg.Connection, incoming_bytes: int) -> None:
        while True:
            usage = await conn.fetchval("SELECT COALESCE(SUM(size_bytes), 0) FROM entries")
            if usage + incoming_bytes <= self.max_bytes:
                return
            victim = await self._pick_eviction_candidate_locked(conn)
            if victim is None:
                return
            await self._evict_locked(conn, victim)

    async def _pick_eviction_candidate_locked(self, conn: asyncpg.Connection) -> str | None:
        now = time.time()
        rows = await conn.fetch("SELECT key, hit_count, last_access FROM entries")

        if not rows:
            return None

        def score(row):
            age_hours = max((now - row["last_access"]) / 3600, 0.1)
            return row["hit_count"] / age_hours

        return min(rows, key=score)["key"]

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM config WHERE key = $1", key)
            return row["value"] if row else default

    async def set_config(self, key: str, value: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO config (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                key, value,
            )

    async def stats(self) -> dict:
        async with self.pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM entries")
        usage = await self._current_usage()
        return {
            "entry_count": count,
            "usage_bytes": usage,
            "usage_gb": round(usage / 1024**3, 2),
            "max_gb": round(self.max_bytes / 1024**3, 2),
            "usage_percent": round(usage / self.max_bytes * 100, 1),
        }
