"""Rastreia estatísticas de atividade do usuário para features de engajamento."""
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import UserStats
from app.observability import get_logger

logger = get_logger(__name__)

_ACTIVITY_FIELDS: dict[str, str] = {
    "verse": "verse_count",
    "explain": "explain_count",
    "reflection": "reflection_count",
    "prayer": "prayer_count",
}


async def get_or_create_user_stats(telegram_user_id: str) -> UserStats:
    """Retorna o UserStats do usuário, criando com defaults se não existir."""
    async with SessionLocal() as session:
        stats = await session.scalar(
            select(UserStats).where(UserStats.telegram_user_id == str(telegram_user_id))
        )
        if not stats:
            stats = UserStats(telegram_user_id=str(telegram_user_id))
            session.add(stats)
            await session.commit()
            await session.refresh(stats)
        return stats


async def register_activity(telegram_user_id: str, activity_type: str) -> dict[str, Any]:
    """Incrementa o contador da atividade, persiste e atualiza o segmento do usuário."""
    field = _ACTIVITY_FIELDS.get(activity_type)

    async with SessionLocal() as session:
        stats = await session.scalar(
            select(UserStats).where(UserStats.telegram_user_id == str(telegram_user_id))
        )
        if not stats:
            stats = UserStats(telegram_user_id=str(telegram_user_id))
            session.add(stats)

        if field:
            setattr(stats, field, (getattr(stats, field) or 0) + 1)

        stats.last_activity_at = datetime.utcnow()
        stats.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(stats)

    try:
        from app.services.segment_service import compute_segment_from_stats, update_user_segment
        new_segment = compute_segment_from_stats(stats.last_activity_at, stats.verse_count or 0, stats.streak_days or 0)
        await update_user_segment(telegram_user_id, new_segment)
    except Exception as seg_err:
        logger.warning(
            "register_activity: falha ao atualizar segmento | user=%s error=%s",
            telegram_user_id, seg_err,
        )

    return {
        "telegram_user_id": telegram_user_id,
        "activity_type": activity_type,
        "verse_count": stats.verse_count,
        "explain_count": stats.explain_count,
        "reflection_count": stats.reflection_count,
        "prayer_count": stats.prayer_count,
        "streak_days": stats.streak_days,
    }
