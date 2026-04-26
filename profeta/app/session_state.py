"""Gerenciamento de sessão por usuário.

Fase 1 : SessionStore wrappea context.user_data (sem alteração de interface).
Fase 2 : JourneyState extraído para core/states.py; TenantSessionStore adicionado
         com backend plugável; register_session faz bridge Telegram→engine.
Fase 3+: troca MemoryBackend por RedisBackend via REDIS_URL sem mudar callers.
"""

from typing import Any, Optional

from telegram.ext import ContextTypes

# Re-exporta JourneyState de core.states para retrocompatibilidade.
# Código legado que faz `from app.session_state import JourneyState` continua funcionando.
# Engine code importa de `app.core.states` diretamente (sem deps de Telegram).
from app.core.states import JourneyState  # noqa: F401

_STATE_KEY = "_journey_state"

# ── Fase 2: Registro global (tenant_id, user_id) → dict de sessão ─────────────
#
# Compartilhado com MemoryBackend (importado de lá para evitar duplicação).
# Com RedisBackend, register_session ainda popula o registry como cache L1,
# e TenantSessionStore salva no Redis em cada `set`.
_SESSION_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {}


def register_session(tenant_id: str, user_id: str, data: dict[str, Any]) -> None:
    """Registra/atualiza o dict de sessão do Telegram no registry global.

    MemoryBackend: compartilha referência ao dict (zero cópia).
    RedisBackend : popula o cache L1 e deixa TenantSessionStore sincronizar
                   com o Redis na próxima escrita.

    Chamar nos entry-points de fluxo de bot_flows onde user_id está disponível.
    """
    _SESSION_REGISTRY[(tenant_id, user_id)] = data


# ── SessionStore — interface legada SYNC (sem alteração) ──────────────────────


class SessionStore:
    """Wrappea context.user_data para acesso desacoplado (sync).

    Não é alterado pela migração Redis — o bot Telegram continua usando
    esta classe. A persistência Redis do lado do bot é responsabilidade de
    um RedisPersistence para python-telegram-bot (fase futura).
    """

    def __init__(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._data: dict[str, Any] = context.user_data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, partial: dict[str, Any]) -> None:
        self._data.update(partial)

    def get_state(self) -> Optional[JourneyState]:
        raw = self._data.get(_STATE_KEY)
        try:
            return JourneyState(raw) if raw else None
        except ValueError:
            return None

    def set_state(self, state: JourneyState) -> None:
        self._data[_STATE_KEY] = state.value


# ── TenantSessionStore — sessão ASYNC com backend plugável (Fase 2+) ──────────


class TenantSessionStore:
    """Store de sessão assíncrono keyed por (tenant_id, user_id).

    Carregamento lazy: o backend é consultado na primeira leitura ou escrita,
    não na construção (que é sync). Escritas são write-through: atualizam o
    dict local E persistem no backend imediatamente.

    MemoryBackend (padrão): zero latência, sem deps externas, sem persistência.
    RedisBackend           : latência de rede, persistência entre restarts,
                             compartilhado entre instâncias.

    Interface duck-type com SessionStore para duck-typing em testes.
    """

    def __init__(self, tenant_id: str, user_id: str) -> None:
        self._key: tuple[str, str] = (tenant_id, user_id)
        self._data: dict[str, Any] = {}
        self._loaded: bool = False

    async def _ensure_loaded(self) -> None:
        """Carrega do backend na primeira operação (lazy)."""
        if self._loaded:
            return
        from app.core.session.factory import get_session_backend
        backend = get_session_backend()
        stored = await backend.load(self._key)
        if stored is not None:
            self._data = stored
        else:
            # Verifica cache L1 (registry populado por register_session)
            cached = _SESSION_REGISTRY.get(self._key)
            if cached is not None:
                self._data = cached
        self._loaded = True

    async def _persist(self) -> None:
        """Write-through: persiste estado atual no backend."""
        from app.core.session.factory import get_session_backend
        backend = get_session_backend()
        _SESSION_REGISTRY[self._key] = self._data   # mantém L1 em sync
        await backend.save(self._key, self._data)

    async def get(self, key: str, default: Any = None) -> Any:
        await self._ensure_loaded()
        return self._data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        await self._ensure_loaded()
        self._data[key] = value
        await self._persist()

    async def update(self, partial: dict[str, Any]) -> None:
        await self._ensure_loaded()
        self._data.update(partial)
        await self._persist()

    async def get_state(self) -> Optional[JourneyState]:
        await self._ensure_loaded()
        raw = self._data.get(_STATE_KEY)
        try:
            return JourneyState(raw) if raw else None
        except ValueError:
            return None

    async def set_state(self, state: JourneyState) -> None:
        await self._ensure_loaded()
        self._data[_STATE_KEY] = state.value
        await self._persist()

    async def delete(self) -> None:
        """Remove toda a sessão (logout / expiração)."""
        self._data = {}
        self._loaded = True
        _SESSION_REGISTRY.pop(self._key, None)
        from app.core.session.factory import get_session_backend
        await get_session_backend().delete(self._key)
