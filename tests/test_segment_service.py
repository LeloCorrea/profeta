from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Subscription, User, UserSegment, UserStats
from app.services.segment_service import _calculate_from_stats


# ── Pure logic tests (no DB) ───────────────────────────────────────────────────

def test_segment_hot():
    today = date.today()
    assert _calculate_from_stats(today, streak_days=7, best_streak=10) == "HOT"


def test_segment_hot_yesterday():
    yesterday = date.today() - timedelta(days=1)
    assert _calculate_from_stats(yesterday, streak_days=5, best_streak=8) == "HOT"


def test_segment_warm_short_streak():
    today = date.today()
    assert _calculate_from_stats(today, streak_days=3, best_streak=3) == "WARM"


def test_segment_warm_two_days_ago():
    two_days = date.today() - timedelta(days=2)
    assert _calculate_from_stats(two_days, streak_days=2, best_streak=2) == "WARM"


def test_segment_at_risk():
    yesterday = date.today() - timedelta(days=1)
    # Was on a great streak (best=7) but just reset (streak=1)
    assert _calculate_from_stats(yesterday, streak_days=1, best_streak=7) == "AT_RISK"


def test_segment_cold_no_activity():
    assert _calculate_from_stats(None, streak_days=0, best_streak=0) == "COLD"


def test_segment_cold_old_activity():
    five_days_ago = date.today() - timedelta(days=5)
    assert _calculate_from_stats(five_days_ago, streak_days=1, best_streak=2) == "COLD"


def test_segment_hot_takes_priority_over_at_risk():
    # Active today with streak >= 5 AND best_streak >= 5 — HOT wins
    today = date.today()
    assert _calculate_from_stats(today, streak_days=6, best_streak=6) == "HOT"


# ── DB-backed tests ────────────────────────────────────────────────────────────

def _make_user(uid: str):
    return User(telegram_user_id=uid, status="active")


def _make_subscription(user_id: int):
    return Subscription(
        user_id=user_id,
        status="active",
        paid_until=datetime.utcnow() + timedelta(days=30),
    )


@pytest.mark.asyncio
async def test_calculate_user_segment_no_stats(db_sessionmaker):
    from app.services.segment_service import calculate_user_segment

    async with db_sessionmaker() as session:
        user = _make_user("u_seg_1")
        session.add(user)
        await session.commit()

    seg = await calculate_user_segment("u_seg_1")
    assert seg == "COLD"  # no stats → COLD


@pytest.mark.asyncio
async def test_calculate_user_segment_hot(db_sessionmaker):
    from app.services.segment_service import calculate_user_segment

    async with db_sessionmaker() as session:
        user = _make_user("u_seg_2")
        session.add(user)
        await session.commit()

        stats = UserStats(
            telegram_user_id="u_seg_2",
            streak_days=7,
            last_activity_at=datetime.utcnow(),
        )
        session.add(stats)
        await session.commit()

    seg = await calculate_user_segment("u_seg_2")
    assert seg == "HOT"


@pytest.mark.asyncio
async def test_update_and_get_user_segment(db_sessionmaker):
    from app.services.segment_service import update_user_segment, get_user_segment

    async with db_sessionmaker() as session:
        user = _make_user("u_seg_3")
        session.add(user)
        await session.commit()

        stats = UserStats(
            telegram_user_id="u_seg_3",
            streak_days=2,
            last_activity_at=datetime.utcnow(),
        )
        session.add(stats)
        await session.commit()

    seg = await update_user_segment("u_seg_3")
    assert seg == "WARM"

    stored = await get_user_segment("u_seg_3")
    assert stored == "WARM"


@pytest.mark.asyncio
async def test_update_segment_is_idempotent(db_sessionmaker):
    from app.services.segment_service import update_user_segment, get_user_segment

    async with db_sessionmaker() as session:
        user = _make_user("u_seg_4")
        session.add(user)
        await session.commit()

        stats = UserStats(
            telegram_user_id="u_seg_4",
            streak_days=3,
            last_activity_at=datetime.utcnow(),
        )
        session.add(stats)
        await session.commit()

    await update_user_segment("u_seg_4")
    await update_user_segment("u_seg_4")
    stored = await get_user_segment("u_seg_4")
    assert stored == "WARM"

    async with db_sessionmaker() as session:
        result = await session.execute(
            select(UserSegment).where(UserSegment.telegram_user_id == "u_seg_4")
        )
        segments = result.scalars().all()
    assert len(segments) == 1  # only one row, not duplicated


@pytest.mark.asyncio
async def test_get_segment_message_hot(db_sessionmaker):
    from app.services.segment_service import update_user_segment, get_segment_message

    async with db_sessionmaker() as session:
        user = _make_user("u_seg_5")
        session.add(user)
        await session.commit()

        stats = UserStats(
            telegram_user_id="u_seg_5",
            streak_days=8,
            last_activity_at=datetime.utcnow(),
        )
        session.add(stats)
        await session.commit()

    await update_user_segment("u_seg_5")
    msg = await get_segment_message("u_seg_5")
    assert "🔥" in msg


@pytest.mark.asyncio
async def test_get_segment_message_warm_is_empty(db_sessionmaker):
    from app.services.segment_service import update_user_segment, get_segment_message

    async with db_sessionmaker() as session:
        user = _make_user("u_seg_6")
        session.add(user)
        await session.commit()

        stats = UserStats(
            telegram_user_id="u_seg_6",
            streak_days=2,
            last_activity_at=datetime.utcnow(),
        )
        session.add(stats)
        await session.commit()

    await update_user_segment("u_seg_6")
    msg = await get_segment_message("u_seg_6")
    assert msg == ""


@pytest.mark.asyncio
async def test_get_segment_message_no_segment_returns_empty(db_sessionmaker):
    from app.services.segment_service import get_segment_message

    msg = await get_segment_message("nonexistent_user_xyz")
    assert msg == ""


@pytest.mark.asyncio
async def test_get_users_by_segment(db_sessionmaker):
    from app.services.segment_service import get_users_by_segment

    async with db_sessionmaker() as session:
        hot_user = _make_user("u_seg_hot_1")
        cold_user = _make_user("u_seg_cold_1")
        session.add_all([hot_user, cold_user])
        await session.commit()

        session.add(_make_subscription(hot_user.id))
        session.add(_make_subscription(cold_user.id))
        await session.commit()

        session.add(UserSegment(telegram_user_id="u_seg_hot_1", segment="HOT"))
        session.add(UserSegment(telegram_user_id="u_seg_cold_1", segment="COLD"))
        await session.commit()

    hot_users = await get_users_by_segment("HOT")
    cold_users = await get_users_by_segment("COLD")

    assert "u_seg_hot_1" in hot_users
    assert "u_seg_cold_1" not in hot_users
    assert "u_seg_cold_1" in cold_users
    assert "u_seg_hot_1" not in cold_users


def test_get_campaign_message_cold():
    from app.services.segment_service import get_campaign_message
    msg = get_campaign_message("COLD")
    assert "versiculo" in msg.lower() or "/versiculo" in msg


def test_get_campaign_message_at_risk():
    from app.services.segment_service import get_campaign_message
    msg = get_campaign_message("AT_RISK")
    assert msg != ""


def test_get_campaign_message_unknown_returns_empty():
    from app.services.segment_service import get_campaign_message
    assert get_campaign_message("UNKNOWN") == ""
