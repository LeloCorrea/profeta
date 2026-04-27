from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Subscription, User, UserProfile


def _make_user(uid="u_profile_1"):
    return User(
        telegram_user_id=uid,
        status="active",
    )


def _make_subscription(user_id: int, active: bool = True):
    return Subscription(
        user_id=user_id,
        status="active" if active else "inactive",
        paid_until=datetime.utcnow() + timedelta(days=30) if active else datetime.utcnow() - timedelta(days=1),
    )


@pytest.mark.asyncio
async def test_track_creates_profile(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, get_user_profile

    async with db_sessionmaker() as session:
        user = _make_user("u_track_1")
        session.add(user)
        await session.commit()

    await track_profile_activity("u_track_1", "verse")
    profile = await get_user_profile("u_track_1")

    assert profile is not None
    assert profile["verse_count"] == 1
    assert profile["explanation_count"] == 0
    assert profile["last_interaction_at"] is not None


@pytest.mark.asyncio
async def test_track_increments_correct_counter(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, get_user_profile

    async with db_sessionmaker() as session:
        user = _make_user("u_track_2")
        session.add(user)
        await session.commit()

    await track_profile_activity("u_track_2", "reflection")
    await track_profile_activity("u_track_2", "reflection")
    await track_profile_activity("u_track_2", "prayer")
    profile = await get_user_profile("u_track_2")

    assert profile["reflection_count"] == 2
    assert profile["prayer_count"] == 1
    assert profile["verse_count"] == 0


@pytest.mark.asyncio
async def test_track_unknown_activity_is_noop(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, get_user_profile

    async with db_sessionmaker() as session:
        user = _make_user("u_track_3")
        session.add(user)
        await session.commit()

    await track_profile_activity("u_track_3", "unknown_type")
    profile = await get_user_profile("u_track_3")
    assert profile is None  # no profile created for unknown activity


@pytest.mark.asyncio
async def test_get_user_preference_returns_dominant(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, get_user_preference

    async with db_sessionmaker() as session:
        user = _make_user("u_pref_1")
        session.add(user)
        await session.commit()

    await track_profile_activity("u_pref_1", "explanation")
    await track_profile_activity("u_pref_1", "explanation")
    await track_profile_activity("u_pref_1", "explanation")
    await track_profile_activity("u_pref_1", "prayer")

    pref = await get_user_preference("u_pref_1")
    assert pref == "explanation"


@pytest.mark.asyncio
async def test_get_user_preference_default_verse_for_new_user(db_sessionmaker):
    from app.services.profile_service import get_user_preference

    pref = await get_user_preference("nonexistent_user_9999")
    assert pref == "verse"


@pytest.mark.asyncio
async def test_get_personalized_nudge_prayer(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, get_personalized_nudge

    async with db_sessionmaker() as session:
        user = _make_user("u_nudge_1")
        session.add(user)
        await session.commit()

    for _ in range(5):
        await track_profile_activity("u_nudge_1", "prayer")

    nudge = await get_personalized_nudge("u_nudge_1")
    assert "oração" in nudge.lower()
    assert nudge.startswith("💡")


@pytest.mark.asyncio
async def test_get_personalized_nudge_no_nudge_for_verse(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, get_personalized_nudge

    async with db_sessionmaker() as session:
        user = _make_user("u_nudge_2")
        session.add(user)
        await session.commit()

    for _ in range(5):
        await track_profile_activity("u_nudge_2", "verse")

    nudge = await get_personalized_nudge("u_nudge_2")
    assert nudge == ""


@pytest.mark.asyncio
async def test_is_user_inactive_recent_interaction(db_sessionmaker):
    from app.services.profile_service import track_profile_activity, is_user_inactive

    async with db_sessionmaker() as session:
        user = _make_user("u_inactive_1")
        session.add(user)
        await session.commit()

    await track_profile_activity("u_inactive_1", "verse")
    inactive = await is_user_inactive("u_inactive_1", days=2)
    assert inactive is False


@pytest.mark.asyncio
async def test_is_user_inactive_old_interaction(db_sessionmaker):
    from app.services.profile_service import is_user_inactive

    async with db_sessionmaker() as session:
        user = _make_user("u_inactive_2")
        session.add(user)
        await session.commit()
        result = await session.execute(select(User.id).where(User.telegram_user_id == "u_inactive_2"))
        user_id = result.scalar_one()

        profile = UserProfile(
            user_id=user_id,
            verse_count=3,
            last_interaction_at=datetime.utcnow() - timedelta(days=5),
        )
        session.add(profile)
        await session.commit()

    inactive = await is_user_inactive("u_inactive_2", days=2)
    assert inactive is True


@pytest.mark.asyncio
async def test_is_user_inactive_no_profile_returns_false(db_sessionmaker):
    from app.services.profile_service import is_user_inactive

    inactive = await is_user_inactive("nonexistent_99999", days=2)
    assert inactive is False


@pytest.mark.asyncio
async def test_get_inactive_active_subscribers(db_sessionmaker):
    from app.services.profile_service import get_inactive_active_subscribers

    async with db_sessionmaker() as session:
        active_user = _make_user("u_sub_active_1")
        inactive_user = _make_user("u_sub_active_2")
        session.add_all([active_user, inactive_user])
        await session.commit()

        sub_active = _make_subscription(active_user.id, active=True)
        sub_inactive_user = _make_subscription(inactive_user.id, active=True)
        session.add_all([sub_active, sub_inactive_user])
        await session.commit()

        # active_user interacted recently
        profile_active = UserProfile(
            user_id=active_user.id,
            verse_count=1,
            last_interaction_at=datetime.utcnow() - timedelta(hours=6),
        )
        # inactive_user hasn't interacted in 5 days
        profile_inactive = UserProfile(
            user_id=inactive_user.id,
            verse_count=1,
            last_interaction_at=datetime.utcnow() - timedelta(days=5),
        )
        session.add_all([profile_active, profile_inactive])
        await session.commit()

    results = await get_inactive_active_subscribers(days=2)
    assert "u_sub_active_2" in results
    assert "u_sub_active_1" not in results
