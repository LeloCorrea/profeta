from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.config import DEFAULT_EXPLANATION_DEPTH
from app.models import FavoriteVerse, User, UserPreference, UserThemeInterest
from app.observability import get_logger, log_event


logger = get_logger(__name__)


async def _get_or_create_user(session, telegram_user_id: str) -> User:
    user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
    if user:
        return user

    user = User(telegram_user_id=str(telegram_user_id), status="active")
    session.add(user)
    await session.flush()
    return user


async def get_or_create_user_preference(session_factory, telegram_user_id: str) -> UserPreference:
    async with session_factory() as session:
        user = await _get_or_create_user(session, telegram_user_id)
        preference = await session.scalar(
            select(UserPreference).where(UserPreference.user_id == user.id)
        )
        if preference:
            return preference

        preference = UserPreference(
            user_id=user.id,
            explanation_depth=DEFAULT_EXPLANATION_DEPTH,
            preferred_delivery="text_audio",
        )
        session.add(preference)
        await session.commit()
        await session.refresh(preference)
        return preference


async def get_user_explanation_depth(session_factory, telegram_user_id: str) -> str:
    preference = await get_or_create_user_preference(session_factory, telegram_user_id)
    return preference.explanation_depth or DEFAULT_EXPLANATION_DEPTH


async def record_theme_interest(
    session_factory,
    telegram_user_id: str,
    theme: str,
    source: str = "journey",
) -> None:
    async with session_factory() as session:
        user = await _get_or_create_user(session, telegram_user_id)
        preference = await session.scalar(
            select(UserPreference).where(UserPreference.user_id == user.id)
        )
        if not preference:
            preference = UserPreference(
                user_id=user.id,
                explanation_depth=DEFAULT_EXPLANATION_DEPTH,
                preferred_delivery="text_audio",
            )
            session.add(preference)

        existing = {
            item.strip()
            for item in (preference.favorite_themes or "").split(",")
            if item.strip()
        }
        existing.add(theme)
        preference.favorite_themes = ", ".join(sorted(existing))
        preference.last_requested_theme = theme
        preference.updated_at = datetime.utcnow()

        session.add(UserThemeInterest(user_id=user.id, theme=theme, source=source))
        await session.commit()

    log_event(logger, "theme_interest_recorded", telegram_user_id=telegram_user_id, theme=theme, source=source)


async def add_favorite_verse(session_factory, telegram_user_id: str, verse: dict[str, Any]) -> bool:
    async with session_factory() as session:
        user = await _get_or_create_user(session, telegram_user_id)

        existing = await session.scalar(
            select(FavoriteVerse).where(
                FavoriteVerse.user_id == user.id,
                FavoriteVerse.book == str(verse["book"]),
                FavoriteVerse.chapter == str(verse["chapter"]),
                FavoriteVerse.verse == str(verse["verse"]),
            )
        )
        if existing:
            return False

        session.add(
            FavoriteVerse(
                user_id=user.id,
                verse_id=verse.get("id"),
                book=str(verse["book"]),
                chapter=str(verse["chapter"]),
                verse=str(verse["verse"]),
                text=str(verse["text"]),
            )
        )
        await session.commit()

    log_event(
        logger,
        "favorite_verse_saved",
        telegram_user_id=telegram_user_id,
        verse_reference=f"{verse['book']} {verse['chapter']}:{verse['verse']}",
    )
    return True


async def list_recent_favorites(session_factory, telegram_user_id: str, limit: int = 5) -> list[str]:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            return []

        items = (
            await session.execute(
                select(FavoriteVerse)
                .where(FavoriteVerse.user_id == user.id)
                .order_by(FavoriteVerse.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

    return [f"{item.book} {item.chapter}:{item.verse}" for item in items]