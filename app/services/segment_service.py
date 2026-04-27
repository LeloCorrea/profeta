"""Segmentação de usuários para campanhas direcionadas."""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import select

from app.db import SessionLocal
from app.models import UserSegment, UserStats
from app.observability import get_logger

logger = get_logger(__name__)

_CAMPAIGN_MESSAGES: dict[str, str] = {
    "COLD": (
        "Sentimos sua falta! 🙏\n\n"
        "Sempre que quiser, use /versiculo para receber uma Palavra do dia."
    ),
    "AT_RISK": (
        "Ei, como você está? 💙\n\n"
        "Estamos aqui com uma Palavra para você. Use /versiculo a qualquer momento."
    ),
}

_SEGMENT_MESSAGES: dict[str, str] = {
    "HOT": "🔥 Você está em chamas! Continue assim — sua sequência espiritual está incrível!",
    "COLD": _CAMPAIGN_MESSAGES["COLD"],
    "AT_RISK": _CAMPAIGN_MESSAGES["AT_RISK"],
    "WARM": "",
}


def _calculate_from_stats(
    last_activity_date: Optional[date],
    streak_days: int = 0,
    best_streak: int = 0,
) -> str:
    """Classifica segmento com suporte a HOT/WARM/AT_RISK/COLD.

    HOT: ativo ontem ou hoje com streak ≥ 5 e melhor sequência ≥ 5.
    AT_RISK: tinha sequência alta (best ≥ 5) mas caiu (atual < metade do best).
    WARM: ativo nos últimos 2 dias com sequência ≥ 2.
    COLD: inativo há 3+ dias ou sem histórico.
    """
    if last_activity_date is None:
        return "COLD"
    today = date.today()
    days_inactive = (today - last_activity_date).days
    if days_inactive >= 3:
        return "COLD"
    if streak_days >= 5 and best_streak >= 5 and days_inactive <= 1:
        return "HOT"
    if best_streak >= 5 and streak_days < best_streak * 0.5 and days_inactive >= 1:
        return "AT_RISK"
    if days_inactive <= 2:
        return "WARM"
    return "COLD"


def compute_segment_from_stats(
    last_activity_at: Optional[datetime],
    verse_count: int = 0,
    streak_days: int = 0,
) -> str:
    """Classifica segmento usando datetime e verse_count (interface nova)."""
    if not last_activity_at:
        return "COLD"
    days_inactive = (datetime.utcnow() - last_activity_at).days
    if days_inactive >= 7:
        return "COLD"
    at_risk_threshold = min(4 if (verse_count >= 10 and streak_days >= 3) else 2, 5)
    if days_inactive >= at_risk_threshold:
        return "AT_RISK"
    return "WARM"


async def calculate_user_segment(telegram_user_id: str) -> str:
    """Calcula segmento de um usuário a partir de suas estatísticas."""
    async with SessionLocal() as session:
        stats = await session.scalar(
            select(UserStats).where(UserStats.telegram_user_id == telegram_user_id)
        )
        if not stats:
            return "COLD"
        last_date = stats.last_activity_at.date() if stats.last_activity_at else None
        streak = stats.streak_days or 0
        return _calculate_from_stats(last_date, streak_days=streak, best_streak=streak)


async def get_or_create_user_segment(telegram_user_id: str) -> UserSegment:
    """Retorna UserSegment do usuário, criando com WARM por padrão se não existir."""
    async with SessionLocal() as session:
        record = await session.scalar(
            select(UserSegment).where(UserSegment.telegram_user_id == str(telegram_user_id))
        )
        if not record:
            record = UserSegment(telegram_user_id=str(telegram_user_id), segment="WARM")
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return record


async def get_user_segment(telegram_user_id: str) -> Optional[str]:
    """Retorna o segmento armazenado do usuário ou None."""
    async with SessionLocal() as session:
        record = await session.scalar(
            select(UserSegment).where(UserSegment.telegram_user_id == str(telegram_user_id))
        )
        return record.segment if record else None


async def get_segment_message(telegram_user_id: str) -> str:
    """Retorna mensagem personalizada para o segmento do usuário."""
    segment = await get_user_segment(telegram_user_id)
    if segment is None:
        return ""
    return _SEGMENT_MESSAGES.get(segment, "")


async def get_users_by_segment(segment: str) -> list[str]:
    """Retorna telegram_user_ids classificados no segmento informado."""
    async with SessionLocal() as session:
        result = await session.execute(
            select(UserSegment.telegram_user_id).where(UserSegment.segment == segment)
        )
        return [row[0] for row in result.all() if row[0]]


def get_campaign_message(segment: str) -> str:
    return _CAMPAIGN_MESSAGES.get(segment, "")


async def update_user_segment(telegram_user_id: str, segment: Optional[str] = None) -> str:
    """Upsert da classificação de segmento.

    Se `segment` não for informado, calcula automaticamente a partir de UserStats.
    Retorna o segmento armazenado.
    """
    if segment is None:
        segment = await calculate_user_segment(telegram_user_id)

    async with SessionLocal() as session:
        record = await session.scalar(
            select(UserSegment).where(UserSegment.telegram_user_id == str(telegram_user_id))
        )
        if record:
            record.segment = segment
            record.updated_at = datetime.utcnow()
        else:
            record = UserSegment(
                telegram_user_id=str(telegram_user_id),
                segment=segment,
            )
            session.add(record)
        await session.commit()

    return segment


async def refresh_segments_for_users(user_ids: list[str]) -> None:
    """Reclassifica o segmento de uma lista de usuários.

    Chamado no job diário antes de enviar campanhas COLD/AT_RISK.
    """
    if not user_ids:
        return

    async with SessionLocal() as session:
        for uid in user_ids:
            stats = await session.scalar(
                select(UserStats).where(UserStats.telegram_user_id == uid)
            )
            last_activity = stats.last_activity_at if stats else None
            vc = (stats.verse_count or 0) if stats else 0
            sd = (stats.streak_days or 0) if stats else 0
            new_segment = compute_segment_from_stats(last_activity, vc, sd)

            record = await session.scalar(
                select(UserSegment).where(UserSegment.telegram_user_id == uid)
            )
            if record:
                record.segment = new_segment
                record.updated_at = datetime.utcnow()
            else:
                session.add(UserSegment(telegram_user_id=uid, segment=new_segment))

        await session.commit()

    logger.info("segment_refresh_completed | users=%d", len(user_ids))
