"""Context resolver — resolve versículo e reflexão sem Telegram context.

Fase 2: usa TenantSessionStore (registry global) como cache L1, com fallback
        ao banco para o último versículo do usuário.
Fase 3+: o backing do TenantSessionStore pode ser trocado por Redis sem
         alterar este módulo.

Não importa nenhum módulo do Telegram.
"""

from typing import Any, Optional

from app.session_state import TenantSessionStore
from app.verse_service import get_last_verse_for_user


async def resolve_verse(tenant_id: str, user_id: str) -> Optional[dict[str, Any]]:
    """Resolve o versículo atual do usuário.

    Ordem de preferência:
    1. Cache em TenantSessionStore (dict compartilhado com context.user_data via Fase 2 bridge)
    2. Último versículo no banco de dados (VerseHistory)
    """
    store = TenantSessionStore(tenant_id, user_id)
    cached = store.get("last_verse")
    if isinstance(cached, dict):
        return cached
    verse = await get_last_verse_for_user(user_id)
    if verse:
        store.set("last_verse", verse)
    return verse


def resolve_reflection(tenant_id: str, user_id: str) -> Optional[dict[str, Any]]:
    """Resolve a reflexão cacheada do usuário (somente memória, sem fallback DB).

    Retorna None se não houver reflexão na sessão — caller decide se gera nova.
    """
    store = TenantSessionStore(tenant_id, user_id)
    payload = store.get("last_reflection")
    return payload if isinstance(payload, dict) else None
