from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import FEATURE_FAVORITES, FEATURE_INLINE_ACTIONS, FEATURE_JOURNEYS


ACTION_EXPLAIN = "action:explain"
ACTION_HEAR_VERSE = "action:hear_verse"
ACTION_PRAY = "action:pray"
ACTION_FAVORITE = "action:favorite"
ACTION_NEW_VERSE = "action:new_verse"
ACTION_HEAR_EXPLANATION = "action:hear_explanation"
ACTION_SHOW_JOURNEYS = "action:show_journeys"
ACTION_CONTINUE_JOURNEY = "action:continue_journey"
JOURNEY_ACTION_PREFIX = "journey:"


def _action_button(label: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=action)


def build_welcome_message() -> str:
    return (
        "🙏 Seja bem-vindo ao Profeta.\n\n"
        "Aqui você recebe Palavra, reflexão e áudio com um tom sereno e cuidadoso.\n\n"
        "Use /versiculo para começar sua jornada de hoje.\n"
        "Use /explicar para aprofundar o último versículo.\n"
        "Use /assinar para ativar ou renovar seu acesso premium."
    )


def build_help_message() -> str:
    return (
        "Comandos disponíveis\n\n"
        "/start - iniciar e ativar acesso por link\n"
        "/versiculo - receber um versículo com áudio\n"
        "/explicar - receber reflexão guiada sobre o último versículo\n"
        "/orar - receber uma oração baseada no último versículo\n"
        "/meuultimo - rever o último versículo enviado\n"
        "/favoritar - salvar o último versículo\n"
        "/favoritos - revisar seus versículos favoritos\n"
        "/trilhas - conhecer jornadas espirituais disponíveis\n"
        "/continuar - retomar sua jornada atual\n"
        "/assinar - ativar ou renovar seu acesso\n"
        "/ajuda - mostrar esta mensagem"
    )


def build_subscription_required_message() -> str:
    return (
        "✨ Seu acesso premium ainda não está ativo.\n\n"
        "Quando desejar, use /assinar para liberar versículos, reflexões em áudio e jornadas guiadas."
    )


def build_subscription_message(payment_link_url: str) -> str:
    return (
        "💳 Ative seu acesso premium ao Profeta\n\n"
        "Assim que o pagamento for confirmado, seu link de ativação ficará pronto automaticamente.\n\n"
        f"{payment_link_url}"
    )


def build_activation_success_message() -> str:
    return (
        "✅ Seu acesso foi ativado com sucesso.\n\n"
        "Você já pode receber um versículo com /versiculo e aprofundar com /explicar."
    )


def build_activation_error_message() -> str:
    return (
        "⚠️ Não consegui concluir sua ativação agora.\n\n"
        "Tente novamente em instantes. Se o problema persistir, gere um novo link de ativação."
    )


def build_no_history_message() -> str:
    return "Ainda não encontrei um versículo no seu histórico. Comece com /versiculo."


def build_verse_unavailable_message() -> str:
    return "⚠️ Não consegui preparar seu versículo agora. Tente novamente em instantes."


def build_reflection_unavailable_message() -> str:
    return (
        "⚠️ Sua reflexão não pôde ser preparada agora.\n\n"
        "Tente novamente em alguns instantes. Seu último versículo continua salvo para você."
    )


def build_audio_unavailable_message() -> str:
    return "⚠️ O texto foi enviado, mas o áudio não ficou pronto desta vez."


def build_prayer_unavailable_message() -> str:
    return "⚠️ Ainda não encontrei um versículo recente para transformar em oração. Use /versiculo primeiro."


def build_favorite_added_message(reference: str) -> str:
    return f"⭐ {reference} foi guardado nos seus favoritos."


def build_favorite_exists_message(reference: str) -> str:
    return f"⭐ {reference} já está nos seus favoritos."


def build_favorites_empty_message() -> str:
    return "Seus favoritos ainda estão vazios. Quando desejar, salve um versículo com /favoritar."


def build_favorites_message(items: list[str]) -> str:
    rendered = "\n".join(f"• {item}" for item in items)
    return f"⭐ Seus favoritos mais recentes\n\n{rendered}"


def build_verse_actions_keyboard() -> InlineKeyboardMarkup | None:
    if not FEATURE_INLINE_ACTIONS:
        return None

    rows = [
        [
            _action_button("Explicar agora", ACTION_EXPLAIN),
            _action_button("Ouvir novamente", ACTION_HEAR_VERSE),
        ],
        [_action_button("Receber oração", ACTION_PRAY)],
    ]

    if FEATURE_FAVORITES:
        rows[-1].append(_action_button("Favoritar", ACTION_FAVORITE))

    if FEATURE_JOURNEYS:
        rows.append(
            [
                _action_button("Continuar jornada", ACTION_CONTINUE_JOURNEY),
                _action_button("Ver trilhas", ACTION_SHOW_JOURNEYS),
            ]
        )

    return InlineKeyboardMarkup(rows)


def build_reflection_actions_keyboard() -> InlineKeyboardMarkup | None:
    if not FEATURE_INLINE_ACTIONS:
        return None

    rows = [
        [
            _action_button("Ouvir reflexão", ACTION_HEAR_EXPLANATION),
            _action_button("Receber oração", ACTION_PRAY),
        ],
        [_action_button("Novo versículo", ACTION_NEW_VERSE)],
    ]

    if FEATURE_FAVORITES:
        rows[-1].append(_action_button("Favoritar", ACTION_FAVORITE))

    return InlineKeyboardMarkup(rows)


def build_prayer_actions_keyboard() -> InlineKeyboardMarkup | None:
    if not FEATURE_INLINE_ACTIONS:
        return None

    return InlineKeyboardMarkup(
        [[_action_button("Novo versículo", ACTION_NEW_VERSE), _action_button("Explicar", ACTION_EXPLAIN)]]
    )


def build_journey_keyboard(journeys: list[object]) -> InlineKeyboardMarkup | None:
    if not FEATURE_INLINE_ACTIONS:
        return None

    rows = [
        [
            InlineKeyboardButton(
                getattr(journey, "title", str(journey)),
                callback_data=f"{JOURNEY_ACTION_PREFIX}{getattr(journey, 'key', journey)}",
            )
        ]
        for journey in journeys
    ]
    return InlineKeyboardMarkup(rows)