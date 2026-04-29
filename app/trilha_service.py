from typing import Optional

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.observability import get_logger, log_event

logger = get_logger(__name__)

# Trilhas fixas ordenadas por chave. A exibição é sempre em ordem alfabética por label.
TRILHA_NAMES: dict[str, str] = {
    "ansiedade": "Ansiedade",
    "casamento": "Casamento",
    "direcao": "Direção",
    "esperanca": "Esperança",
    "familia": "Família",
    "fe": "Fé",
    "forca": "Força",
    "gratidao": "Gratidão",
    "perdao": "Perdão",
    "proposito": "Propósito",
    "sabedoria": "Sabedoria",
}

TRILHA_RANDOM = "aleatorio"


def list_trilhas() -> list[tuple[str, str]]:
    """Retorna lista de (chave, label) ordenada alfabeticamente, com ✨ Aleatório no fim."""
    sorted_items = sorted(TRILHA_NAMES.items(), key=lambda x: x[1])
    sorted_items.append((TRILHA_RANDOM, "✨ Aleatório"))
    return sorted_items


def get_trilha_label(trilha_key: Optional[str]) -> Optional[str]:
    """Retorna o label de exibição da trilha, ou None para null/aleatório."""
    if not trilha_key or trilha_key == TRILHA_RANDOM:
        return None
    return TRILHA_NAMES.get(trilha_key)


async def get_user_trilha(telegram_user_id: str) -> Optional[str]:
    """Retorna a chave da trilha selecionada pelo usuário, ou None (aleatório)."""
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == str(telegram_user_id))
        )
        return user.selected_trilha if user else None


async def set_user_trilha(telegram_user_id: str, trilha_key: Optional[str]) -> None:
    """Persiste a seleção de trilha do usuário. TRILHA_RANDOM ou None = modo aleatório."""
    actual_key = None if (not trilha_key or trilha_key == TRILHA_RANDOM) else trilha_key
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == str(telegram_user_id))
        )
        if user:
            user.selected_trilha = actual_key
            await session.commit()
    log_event(
        logger,
        "user_trilha_set",
        telegram_user_id=telegram_user_id,
        trilha=actual_key,
    )
