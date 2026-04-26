"""State machine da conversa espiritual — sem dependências do Telegram.

Define as transições canônicas de estado e resolve a próxima ação a executar.
Não importa nenhum módulo do Telegram; pode ser testado isoladamente.

Progressão:
    VERSE → EXPLANATION → REFLECTION → PRAYER (→ VERSE, recomeça)

A função `next_action_for_state` retorna o nome da ação que o engine deve
executar dado o estado atual do usuário — é a única lógica de roteamento
centralizada do SaaS.
"""

from typing import Optional

from app.core.states import JourneyState

# Mapeamento estado atual → próxima ação do engine
_STATE_TO_ACTION: dict[Optional[JourneyState], str] = {
    None: "explanation_get",           # nenhum estado → começa pela explicação do versículo atual
    JourneyState.VERSE: "explanation_get",
    JourneyState.EXPLANATION: "reflection_get",
    JourneyState.REFLECTION: "prayer_get",
    JourneyState.PRAYER: "verse_select",   # ciclo completo → novo versículo
}

# Mapeamento estado atual → próximo estado após a ação
_TRANSITIONS: dict[Optional[JourneyState], JourneyState] = {
    None: JourneyState.EXPLANATION,
    JourneyState.VERSE: JourneyState.EXPLANATION,
    JourneyState.EXPLANATION: JourneyState.REFLECTION,
    JourneyState.REFLECTION: JourneyState.PRAYER,
    JourneyState.PRAYER: JourneyState.VERSE,
}


def next_action_for_state(state: Optional[JourneyState]) -> str:
    """Retorna o nome da ação a executar dado o estado atual da conversa."""
    return _STATE_TO_ACTION.get(state, "explanation_get")


def next_state(current: Optional[JourneyState]) -> JourneyState:
    """Retorna o estado resultante após executar a ação do estado atual."""
    return _TRANSITIONS.get(current, JourneyState.VERSE)
