from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EngineInput:
    """Contrato canônico de entrada para todas as ações do engine.

    Qualquer adapter (Telegram bot, REST API, CLI) deve normalizar sua
    requisição nesta estrutura antes de chamar o engine.

    tenant_id : identifica o tenant (ex: "profeta", "esperanca_branca")
    user_id   : identificador do usuário independente de plataforma
                (telegram_user_id nesta fase; oauth_sub na Fase 5+)
    action    : nome da ação a executar (ver EngineFacade._handlers)
    payload   : dados adicionais dependentes da ação
    """

    tenant_id: str
    user_id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineOutput:
    """Contrato canônico de saída retornado pelo engine facade.

    Adapters (bot, API) traduzem este objeto para seu formato nativo.

    success : False indica erro controlado; caller deve verificar `error`
    action  : echo da ação solicitada (facilita logging no adapter)
    data    : payload de resposta dependente da ação
    error   : código/mensagem de erro quando success=False
    """

    success: bool
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
