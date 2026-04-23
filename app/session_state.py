"""Gerenciamento de sessão por usuário.

Fase 1: SessionStore wrappea context.user_data do Telegram (sem alteração).
Fase 2: JourneyState extraído para core/states.py (re-exportado aqui para
        retrocompatibilidade); TenantSessionStore adicionado sem deps do
        Telegram; registro global permite engine ler estado sem context.
Fase 3+: troca o backing de _SESSION_REGISTRY por Redis sem mudar a interface.
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
# Permite que o engine leia/escreva estado sem precisar de um context object
# do Telegram. Fase 3+ substitui o dict por um cliente Redis sem mudar a API.
_SESSION_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {}


def register_session(tenant_id: str, user_id: str, data: dict[str, Any]) -> None:
    """Registra o dict de sessão do Telegram no registro global (Fase 2 bridge).

    Chamado por bot_flows nos entry-points de fluxo onde user_id já está
    disponível. Compartilha o mesmo objeto dict — sem cópia, sem sync.
    """
    _SESSION_REGISTRY[(tenant_id, user_id)] = data


# ── SessionStore — interface legada (sem alteração de comportamento) ────────────


class SessionStore:
    """Wrappea context.user_data para acesso desacoplado à sessão.

    Todos os acessos a chaves de sessão passam por esta classe para que o
    backing store (atualmente o dict in-process do python-telegram-bot) possa
    ser trocado por Redis sem alterar nenhum caller.
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


# ── TenantSessionStore — sessão sem deps do Telegram (Fase 2+) ────────────────


class TenantSessionStore:
    """Store de sessão keyed por (tenant_id, user_id) — sem Telegram context.

    Fase 2: backed pelo mesmo dict registrado via register_session(); se o
            usuário ainda não passou por um flow Telegram nesta sessão, cria
            um dict vazio no registro (operação segura — sem efeito colateral).
    Fase 3+: swap do backing para Redis sem mudar esta interface.

    Interface idêntica ao SessionStore para permitir duck typing em testes.
    """

    def __init__(self, tenant_id: str, user_id: str) -> None:
        key = (tenant_id, user_id)
        if key not in _SESSION_REGISTRY:
            _SESSION_REGISTRY[key] = {}
        self._data = _SESSION_REGISTRY[key]

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
