import asyncio

import pytest

from subscription_manager import FREE_DOWNLOADS, FREE_SEASONS, SEASON_SIZE, SubscriptionManager


@pytest.fixture
async def sub_manager(pool):
    m = SubscriptionManager(pool)
    await m.init()
    return m


async def test_new_user_can_download(sub_manager):
    ok, reason = await sub_manager.try_consume_download(111)
    assert ok is True
    assert reason is None


async def test_free_allowance_exhausted(sub_manager):
    allowance = FREE_SEASONS * SEASON_SIZE + FREE_DOWNLOADS
    for _ in range(allowance):
        ok, _ = await sub_manager.try_consume_download(222)
        assert ok is True

    ok, reason = await sub_manager.try_consume_download(222)

    assert ok is False
    assert reason is not None


async def test_concurrent_consume_does_not_exceed_allowance(sub_manager):
    allowance = FREE_SEASONS * SEASON_SIZE + FREE_DOWNLOADS
    results = await asyncio.gather(
        *[sub_manager.try_consume_download(333) for _ in range(allowance * 2)]
    )
    successes = sum(1 for ok, _ in results if ok)
    assert successes == allowance


async def test_active_subscription_bypasses_allowance(sub_manager):
    await sub_manager.activate_subscription(444, stars=50, charge_id="charge-1")

    ok, reason = await sub_manager.try_consume_download(444)

    assert ok is True
    assert reason is None


async def test_activate_subscription_is_idempotent_on_charge_id(sub_manager):
    first = await sub_manager.activate_subscription(555, stars=50, charge_id="dup-charge")
    second = await sub_manager.activate_subscription(555, stars=50, charge_id="dup-charge")

    assert first is True
    assert second is False

    user = await sub_manager.get_user(555)
    assert user["total_stars_spent"] == 50


async def test_can_download_is_read_only(sub_manager):
    ok, _ = await sub_manager.can_download(666)
    ok_again, _ = await sub_manager.can_download(666)

    assert ok is True
    assert ok_again is True
    user = await sub_manager.get_user(666)
    assert user["download_count"] == 0
