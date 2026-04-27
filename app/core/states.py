from enum import Enum


class JourneyState(str, Enum):
    """Estado da conversa espiritual — definição livre de dependências do Telegram.

    Usado tanto pela camada legada (SessionStore em session_state.py, que
    re-exporta daqui para compatibilidade retroativa) quanto pelo novo engine
    (importado diretamente daqui, sem transitar por módulos com deps do bot).

    Progressão canônica:
        VERSE → EXPLANATION → REFLECTION → PRAYER (→ VERSE …)
    """

    VERSE = "verse_received"
    EXPLANATION = "explanation_done"
    REFLECTION = "reflection_done"
    PRAYER = "prayer_done"
