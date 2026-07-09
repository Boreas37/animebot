import time

import asyncpg

FREE_DOWNLOADS = 3
SEASON_SIZE = 30
FREE_SEASONS = 1
SUBSCRIPTION_PRICE_STARS = 50
SUBSCRIPTION_DAYS = 30


class SubscriptionManager:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def init(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    download_count INTEGER NOT NULL DEFAULT 0,
                    subscription_expires DOUBLE PRECISION,
                    total_stars_spent INTEGER NOT NULL DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    charge_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    stars INTEGER NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                )
            """)

    def _free_allowance(self) -> int:
        return FREE_SEASONS * SEASON_SIZE + FREE_DOWNLOADS

    async def get_user(self, user_id: int) -> dict:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, download_count) VALUES ($1, 0) ON CONFLICT (user_id) DO NOTHING",
                user_id,
            )
            row = await conn.fetchrow(
                "SELECT user_id, download_count, subscription_expires, total_stars_spent "
                "FROM users WHERE user_id = $1",
                user_id,
            )
            return dict(row)

    async def try_consume_download(self, user_id: int) -> tuple[bool, str | None]:
        now = time.time()
        allowance = self._free_allowance()
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, download_count) VALUES ($1, 0) ON CONFLICT (user_id) DO NOTHING",
                user_id,
            )
            row = await conn.fetchrow(
                """
                UPDATE users
                SET download_count = download_count + 1
                WHERE user_id = $1
                  AND (
                    (subscription_expires IS NOT NULL AND subscription_expires > $2)
                    OR download_count < $3
                  )
                RETURNING download_count
                """,
                user_id, now, allowance,
            )
        if row is not None:
            return True, None
        return False, (
            f"{allowance} ücretsiz indirmen bitti. "
            f"Aylık {SUBSCRIPTION_PRICE_STARS} ⭐ ile aboneliğini başlat!"
        )

    async def can_download(self, user_id: int) -> tuple[bool, str | None]:
        user = await self.get_user(user_id)
        now = time.time()
        if user["subscription_expires"] and user["subscription_expires"] > now:
            return True, None
        if user["download_count"] < self._free_allowance():
            return True, None
        return False, (
            f"{self._free_allowance()} ücretsiz indirmen bitti. "
            f"Aylık {SUBSCRIPTION_PRICE_STARS} ⭐ ile aboneliğini başlat!"
        )

    async def activate_subscription(self, user_id: int, stars: int, charge_id: str) -> bool:
        now = time.time()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                inserted = await conn.fetchval(
                    "INSERT INTO payments (charge_id, user_id, stars, created_at) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (charge_id) DO NOTHING RETURNING charge_id",
                    charge_id, user_id, stars, now,
                )
                if inserted is None:
                    return False

                await conn.execute(
                    "INSERT INTO users (user_id, download_count) VALUES ($1, 0) ON CONFLICT (user_id) DO NOTHING",
                    user_id,
                )
                row = await conn.fetchrow(
                    "SELECT subscription_expires FROM users WHERE user_id = $1", user_id
                )
                current = row["subscription_expires"] if row and row["subscription_expires"] else now
                new_expiry = max(current, now) + SUBSCRIPTION_DAYS * 86400
                await conn.execute(
                    "UPDATE users SET subscription_expires = $1, total_stars_spent = total_stars_spent + $2 "
                    "WHERE user_id = $3",
                    new_expiry, stars, user_id,
                )
        return True

    async def info(self, user_id: int) -> str:
        user = await self.get_user(user_id)
        now = time.time()
        expire = user["subscription_expires"]
        remaining = ""
        if expire and expire > now:
            days = int((expire - now) / 86400)
            remaining = f"Kalan süre: {days} gün"
        text = (
            f"📊 Hesabın\n"
            f"Ücretsiz indirme: {user['download_count']}/{self._free_allowance()}\n"
            f"Toplam ⭐: {user['total_stars_spent']}"
        )
        if remaining:
            text += f"\n{remaining}"
        return text
