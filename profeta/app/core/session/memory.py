"""MemoryBackend — implementação in-process do SessionBackend.

Usa o _SESSION_REGISTRY global de session_state.py como backing store.
Comportamento idêntico ao da Fase 2: sem I/O, zero latência extra.

Adequado para:
- Desenvolvimento local
- Instância única de bot
- Testes (sem deps externas)

Limitação: estado perdido no restart e não compartilhado entre instâncias.
"""

from typing import Any, Optional

# O registro é importado aqui e não redefinido; dict compartilhado com TenantSessionStore.
from app.session_state import _SESSION_REGISTRY


class MemoryBackend:
    """Backend de sessão puramente in-process."""

    async def load(self, key: tuple[str, str]) -> Optional[dict[str, Any]]:
        return _SESSION_REGISTRY.get(key)

    async def save(self, key: tuple[str, str], data: dict[str, Any]) -> None:
        _SESSION_REGISTRY[key] = data

    async def delete(self, key: tuple[str, str]) -> None:
        _SESSION_REGISTRY.pop(key, None)
