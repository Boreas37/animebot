import asyncpg
import pytest

TEST_DSN = "postgresql://postgres:test@localhost:55432/animebot_test"

TABLES = [
    "entries", "config", "users", "payments", "telegram_cache", "preload_checklist",
]


@pytest.fixture(scope="session")
async def pool():
    p = await asyncpg.create_pool(dsn=TEST_DSN, min_size=1, max_size=5)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _clean_tables(pool):
    async with pool.acquire() as conn:
        for table in TABLES:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    yield
