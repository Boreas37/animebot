from db import create_pool


async def test_create_pool_connects():
    pool = await create_pool("postgresql://postgres:test@localhost:55432/animebot_test")
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
        assert result == 1
    finally:
        await pool.close()
