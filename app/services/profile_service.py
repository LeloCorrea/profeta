"""Analytics de engajamento usando campos existentes da tabela users."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Subscription, User, UserProfile
from app.observability import get_logger

logger = get_logger(__name__)

_NUDGE_MESSAGES: dict[str, str] = {
    "prayer": "💡 Que tal uma oração especial hoje?",
    "explanation": "💡 Você gosta de entender a Palavra — explore uma explicação hoje!",
    "reflection": "💡 Um momento de reflexão pode transformar seu dia.",
}


async def track_profile_activity(telegram_user_id: str, activity_type: str) -> None:
    known = {"verse", "explanation", "reflection", "prayer"}
    if activity_type not in known:
        return

    async with SessionLocal() as session:
        result = await session.execute(
            select(User.id).where(User.telegram_user_id == telegram_user_id)
        )
        user_id = result.scalar_one_or_none()
        if user_id is None:
            return

        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        if profile is None:
            profile = UserProfile(user_id=user_id)
            session.add(profile)

        if activity_type == "verse":
            profile.verse_count = (profile.verse_count or 0) + 1
        elif activity_type == "explanation":
            profile.explanation_count = (profile.explanation_count or 0) + 1
        elif activity_type == "reflection":
            profile.reflection_count = (profile.reflection_count or 0) + 1
        elif activity_type == "prayer":
            profile.prayer_count = (profile.prayer_count or 0) + 1

        profile.last_interaction_at = datetime.utcnow()
        await session.commit()


async def get_user_profile(telegram_user_id: str) -> dict | None:
    async with SessionLocal() as session:
        result = await session.execute(
            select(User.id).where(User.telegram_user_id == telegram_user_id)
        )
        user_id = result.scalar_one_or_none()
        if user_id is None:
            return None

        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        if profile is None:
            return None

        return {
            "verse_count": profile.verse_count,
            "explanation_count": profile.explanation_count,
            "reflection_count": profile.reflection_count,
            "prayer_count": profile.prayer_count,
            "last_interaction_at": profile.last_interaction_at,
        }


async def get_user_preference(telegram_user_id: str) -> str:
    profile = await get_user_profile(telegram_user_id)
    if profile is None:
        return "verse"

    counts = {
        "verse": profile["verse_count"],
        "explanation": profile["explanation_count"],
        "reflection": profile["reflection_count"],
        "prayer": profile["prayer_count"],
    }
    max_type = max(counts, key=lambda k: counts[k])
    return max_type if counts[max_type] > 0 else "verse"


async def get_personalized_nudge(telegram_user_id: str) -> str:
    pref = await get_user_preference(telegram_user_id)
    return _NUDGE_MESSAGES.get(pref, "")


async def is_user_inactive(telegram_user_id: str, days: int = 2) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(
            select(User.id).where(User.telegram_user_id == telegram_user_id)
        )
        user_id = result.scalar_one_or_none()
        if user_id is None:
            return False

        profile = await session.scalar(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        if profile is None or profile.last_interaction_at is None:
            return False

        cutoff = datetime.utcnow() - timedelta(days=days)
        return profile.last_interaction_at < cutoff


async def get_inactive_active_subscribers(days: int = 2) -> list[str]:
    """Retorna telegram_user_ids de assinantes ativos sem interação recente."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    now = datetime.utcnow()

    async with SessionLocal() as session:
        stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until > now)
            .where(UserProfile.last_interaction_at.isnot(None))
            .where(UserProfile.last_interaction_at <= cutoff)
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]
