import time

import asyncpg


class TelegramCache:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def init(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_cache (
                    key TEXT PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """)

    async def get(self, key: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT chat_id, message_id, file_id FROM telegram_cache WHERE key = $1", key
            )
            if row is None:
                return None
            return {"chat_id": row["chat_id"], "message_id": row["message_id"], "file_id": row["file_id"]}

    async def put(self, key: str, chat_id: int, message_id: int, file_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO telegram_cache (key, chat_id, message_id, file_id, created_at)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (key) DO UPDATE SET
                     chat_id = EXCLUDED.chat_id,
                     message_id = EXCLUDED.message_id,
                     file_id = EXCLUDED.file_id,
                     created_at = EXCLUDED.created_at""",
                key, chat_id, message_id, file_id, time.time(),
            )

    async def exists(self, key: str) -> bool:
        return await self.get(key) is not None

    async def count(self) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM telegram_cache")
