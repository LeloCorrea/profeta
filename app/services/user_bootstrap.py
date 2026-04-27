"""Bootstrap de dados de engajamento: garante registros válidos por usuário.

Chamado em cada interação via ensure_user_record() no bot.py.
Idempotente — seguro chamar múltiplas vezes.
"""
from app.db import SessionLocal
from app.models import UserSegment, UserStats
from app.observability import get_logger
from sqlalchemy import select

logger = get_logger(__name__)


async def ensure_user_initialized(telegram_user_id: str) -> None:
    """Garante que UserStats e UserSegment existem para o usuário.

    Cria ambos em transação única. Em produção, falhas propagam para
    o caller — garantindo visibilidade real do problema.
    """
    uid = str(telegram_user_id)
    try:
        async with SessionLocal() as session:
            async with session.begin():
                stats = await session.scalar(
                    select(UserStats).where(UserStats.telegram_user_id == uid)
                )
                if not stats:
                    session.add(UserStats(telegram_user_id=uid))
                    logger.info("user_stats_created | user=%s", uid)

                segment = await session.scalar(
                    select(UserSegment).where(UserSegment.telegram_user_id == uid)
                )
                if not segment:
                    session.add(UserSegment(telegram_user_id=uid, segment="WARM"))
                    logger.info("user_segment_created | user=%s", uid)
    except Exception:
        logger.warning("ensure_user_initialized failed | user=%s", uid, exc_info=True)
        from app.config import ENV
        if ENV.lower() in {"prod", "production"}:
            raise
