from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from typing import Optional

from app.config import CREDIT_PACKAGES, CURRENT_TENANT, FEATURE_FAVORITES, FEATURE_INLINE_ACTIONS, FEATURE_JOURNEYS

_IMAGE_BUTTON_LABEL = "🎨 Criar imagem (1 crédito)"
from app.tenant_config import TenantBranding, TenantConfig


ACTION_EXPLAIN = "action:explain"
ACTION_HEAR_VERSE = "action:hear_verse"
ACTION_PRAY = "action:pray"
ACTION_FAVORITE = "action:favorite"
ACTION_NEW_VERSE = "action:new_verse"
ACTION_HEAR_EXPLANATION = "action:hear_explanation"
ACTION_SHOW_JOURNEYS = "action:show_journeys"
ACTION_CONTINUE_JOURNEY = "action:continue_journey"
JOURNEY_ACTION_PREFIX = "journey:"

CREATE_IMAGE_ACTION_PREFIX = "action:create_image|"
CONFIRM_IMAGE_ACTION_PREFIX = "action:confirm_image|"
ACTION_CANCEL_IMAGE = "action:cancel_image"
BUY_CREDITS_ACTION_PREFIX = "action:buy_credits|"


def _action_button(label: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=action)


def build_welcome_message(cfg: Optional[TenantConfig] = None) -> str:
    b = (cfg or CURRENT_TENANT).branding
    return (
        f"Seja bem-vindo ao {b.app_name}. 🙏\n\n"
        f"Aqui você recebe Palavra, reflexão e áudio com um tom {b.content_tone}"
        " — para fortalecer sua fé todos os dias.\n\n"
        f"📖 {b.welcome_verse_ref}\n\n"
        f"\"{b.welcome_verse_text}\"\n\n"
        "Hoje pode ser o começo de uma nova jornada com Deus.\n\n"
        "Use /versiculo para receber sua Palavra de hoje.\n"
        "Use /explicar para aprofundar o último versículo.\n"
        "Use /reflexao para refletir sobre essa Palavra.\n"
        "Use /orar para receber uma oração guiada para o seu momento.\n"
        "Use /ajuda para conhecer tudo com calma.\n"
        "Use /assinar para ativar ou renovar seu acesso premium."
    )


def build_help_message() -> str:
    return (
        "Comandos disponíveis\n\n"
        "/start - iniciar e ativar acesso por link\n"
        "/versiculo - receber um versículo com áudio\n"
        "/explicar - explicação bíblica e contextual do último versículo\n"
        "/reflexao - reflexão contemplativa profunda sobre o último versículo\n"
        "/orar - oração baseada no último versículo\n"
        "/meuultimo - rever o último versículo enviado\n"
        "/favoritar - salvar o último versículo\n"
        "/favoritos - revisar seus versículos favoritos\n"
        "/trilhas - conhecer jornadas espirituais disponíveis\n"
        "/continuar - retomar sua jornada atual\n"
        "/buscar - buscar versículos por tema\n"
        "/meuplano - ver status da sua assinatura\n"
        "/assinar - ativar ou renovar seu acesso\n"
        "/ajuda - mostrar esta mensagem"
    )


def build_subscription_required_message(
    payment_url: str = "",
    cfg: Optional[TenantConfig] = None,
) -> str:
    b = (cfg or CURRENT_TENANT).branding
    base = f"✨ Este recurso é exclusivo para assinantes do {b.app_name}.\n\n{b.subscription_pitch}"
    if payment_url:
        return f"{base}\n\n{payment_url}"
    return f"{base}\n\nUse /assinar para ativar seu acesso."


def build_subscription_message(
    payment_link_url: str,
    cfg: Optional[TenantConfig] = None,
) -> str:
    b = (cfg or CURRENT_TENANT).branding
    return (
        f"💳 Ative seu acesso premium ao {b.app_name}\n\n"
        "Assim que o pagamento for confirmado, seu link de ativação ficará pronto automaticamente.\n\n"
        f"{payment_link_url}"
    )


def build_payment_message(
    invoice_url: str,
    pix_code: Optional[str] = None,
    value: Optional[float] = None,
    fallback: bool = False,
) -> str:
    value_line = f"Valor: R$ {value:.2f} (acesso mensal)\n\n" if value else ""

    if fallback or not pix_code:
        return (
            "💳 Ative seu acesso premium ao Profeta\n\n"
            f"{value_line}"
            "Assim que o pagamento for confirmado, seu acesso é ativado automaticamente.\n\n"
            f"{invoice_url}"
        )

    return (
        "💳 Seu pagamento PIX foi gerado\n\n"
        f"{value_line}"
        f"{invoice_url}\n\n"
        "Ou use o código PIX (copia e cola):\n\n"
        f"`{pix_code}`\n\n"
        "Assim que o pagamento for confirmado, seu acesso é ativado automaticamente."
    )


def build_activation_success_message() -> str:
    return (
        "✅ Seu acesso foi ativado com sucesso.\n\n"
        "Você já pode receber um versículo com /versiculo, aprofundar com /explicar e retomar seu ritmo espiritual com /continuar."
    )


def build_activation_error_message() -> str:
    return (
        "⚠️ Não consegui concluir sua ativação agora.\n\n"
        "Tente novamente em instantes. Se o problema persistir, gere um novo link de ativação."
    )


def build_no_history_message() -> str:
    return "Ainda não encontrei um versículo no seu histórico. Comece com /versiculo e, depois disso, eu sigo com /explicar, /orar e /favoritar."


def build_verse_unavailable_message() -> str:
    return "⚠️ Não consegui preparar seu versículo agora. Tente novamente em instantes."


def build_reflection_unavailable_message() -> str:
    return (
        "⚠️ Sua reflexão não pôde ser preparada agora.\n\n"
        "Tente novamente em alguns instantes. Seu último versículo continua salvo para você, então basta repetir /explicar quando quiser."
    )


def build_audio_unavailable_message() -> str:
    return "⚠️ O texto foi enviado, mas o áudio não ficou pronto desta vez. Se desejar, tente novamente em instantes sem perder seu conteúdo."


def build_prayer_unavailable_message() -> str:
    return "⚠️ Ainda não encontrei um versículo recente para transformar em oração. Use /versiculo primeiro e, em seguida, eu preparo a oração para você."


def build_favorite_added_message(reference: str) -> str:
    return f"⭐ {reference} foi guardado nos seus favoritos."


def build_favorite_exists_message(reference: str) -> str:
    return f"⭐ {reference} já está nos seus favoritos."


def build_favorites_empty_message() -> str:
    return "Seus favoritos ainda estão vazios. Quando um versículo tocar seu coração, use /favoritar e eu guardo para você."


def build_favorites_message(items: list[str]) -> str:
    rendered = "\n".join(f"• {item}" for item in items)
    return f"⭐ Seus favoritos mais recentes\n\n{rendered}"


def build_verse_actions_keyboard(
    cfg: Optional[TenantConfig] = None,
    image_content_id: Optional[str] = None,
) -> Optional[InlineKeyboardMarkup]:
    _cfg = cfg or CURRENT_TENANT
    if not _cfg.feature_inline_actions:
        return None

    rows = [
        [
            _action_button("Explicar agora", ACTION_EXPLAIN),
            _action_button("Ouvir novamente", ACTION_HEAR_VERSE),
        ],
        [_action_button("Receber oração", ACTION_PRAY)],
    ]

    if _cfg.feature_favorites:
        rows[-1].append(_action_button("Favoritar", ACTION_FAVORITE))

    if _cfg.feature_journeys:
        rows.append(
            [
                _action_button("Continuar jornada", ACTION_CONTINUE_JOURNEY),
                _action_button("Ver trilhas", ACTION_SHOW_JOURNEYS),
            ]
        )

    if _cfg.feature_share and image_content_id:
        rows.append([_action_button(_IMAGE_BUTTON_LABEL, f"{CREATE_IMAGE_ACTION_PREFIX}verse|{image_content_id}")])

    return InlineKeyboardMarkup(rows)


def build_explanation_actions_keyboard(
    cfg: Optional[TenantConfig] = None,
    image_content_id: Optional[str] = None,
) -> Optional[InlineKeyboardMarkup]:
    _cfg = cfg or CURRENT_TENANT
    if not _cfg.feature_inline_actions:
        return None

    rows = [
        [_action_button("Continuar jornada", ACTION_CONTINUE_JOURNEY)],
        [
            _action_button("Ouvir novamente", ACTION_HEAR_EXPLANATION),
            _action_button("Receber oração", ACTION_PRAY),
        ],
    ]

    if _cfg.feature_favorites:
        rows[-1].append(_action_button("Favoritar", ACTION_FAVORITE))

    if _cfg.feature_share and image_content_id:
        rows.append([_action_button(_IMAGE_BUTTON_LABEL, f"{CREATE_IMAGE_ACTION_PREFIX}explain|{image_content_id}")])

    return InlineKeyboardMarkup(rows)


def build_reflection_actions_keyboard(
    cfg: Optional[TenantConfig] = None,
    image_content_id: Optional[str] = None,
) -> Optional[InlineKeyboardMarkup]:
    _cfg = cfg or CURRENT_TENANT
    if not _cfg.feature_inline_actions:
        return None

    rows = [
        [
            _action_button("Ouvir reflexão", ACTION_HEAR_EXPLANATION),
            _action_button("Receber oração", ACTION_PRAY),
        ],
        [_action_button("Continuar jornada", ACTION_CONTINUE_JOURNEY)],
    ]

    if _cfg.feature_favorites:
        rows[-1].append(_action_button("Favoritar", ACTION_FAVORITE))

    if _cfg.feature_share and image_content_id:
        rows.append([_action_button(_IMAGE_BUTTON_LABEL, f"{CREATE_IMAGE_ACTION_PREFIX}reflect|{image_content_id}")])

    return InlineKeyboardMarkup(rows)


def build_prayer_actions_keyboard(
    cfg: Optional[TenantConfig] = None,
    image_content_id: Optional[str] = None,
) -> Optional[InlineKeyboardMarkup]:
    _cfg = cfg or CURRENT_TENANT
    if not _cfg.feature_inline_actions:
        return None

    rows = [[_action_button("Novo versículo", ACTION_NEW_VERSE), _action_button("Explicar", ACTION_EXPLAIN)]]

    if _cfg.feature_share and image_content_id:
        rows.append([_action_button(_IMAGE_BUTTON_LABEL, f"{CREATE_IMAGE_ACTION_PREFIX}prayer|{image_content_id}")])

    return InlineKeyboardMarkup(rows)


def build_image_confirm_keyboard(content_type: str, content_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data=f"{CONFIRM_IMAGE_ACTION_PREFIX}{content_type}|{content_id}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=ACTION_CANCEL_IMAGE),
        ]
    ])


def build_rate_limit_message() -> str:
    return "⏳ Você enviou muitas solicitações. Aguarde um momento antes de tentar novamente."


def build_search_results_message(keyword: str, verses: list[dict]) -> str:
    def _ref(v: dict) -> str:
        return f"{v['book']} {v['chapter']}:{v['verse']}"

    items = "\n\n".join(f"📖 {_ref(v)}\n\u201c{v['text']}\u201d" for v in verses)
    return f"🔍 Resultado para \u201c{keyword}\u201d\n\n{items}"


def build_search_empty_message(keyword: str) -> str:
    return f"Não encontrei versículos com \u201c{keyword}\u201d. Tente outro tema ou palavra-chave."


def build_admin_status_message(stats: dict) -> str:
    expiring = stats.get("expiring_7_days", 0)
    expiring_line = f"\n⏳ Expirando em 7 dias: {expiring}" if expiring else ""
    return (
        "📊 Status do Profeta\n\n"
        f"Usuários cadastrados: {stats.get('total_users', 0)}\n"
        f"Assinaturas ativas: {stats.get('active_subscriptions', 0)}"
        f"{expiring_line}"
    )


def build_admin_users_message(users: list[dict]) -> str:
    if not users:
        return "Nenhum usuário ativo encontrado."
    lines = "\n".join(
        f"• @{u['username']} ({u['telegram_user_id']}) — desde {u['created_at']}"
        for u in users
    )
    return f"👥 Usuários ativos recentes\n\n{lines}"


def build_admin_image_requests_message(requests: list[dict]) -> str:
    if not requests:
        return "Nenhum pedido de imagem encontrado."
    lines = []
    for r in requests:
        price_str = f"R${r['price']:.2f}".replace(".", ",")
        pay = "✅ pago" if r["payment_status"] == "paid" else "⏳ pendente"
        img = "🖼 pronto" if r["status"] == "done" else f"🔄 {r['status']}"
        lines.append(f"• #{r['id']} | {r['telegram_id']} | {r['content_type']} | {price_str} | {pay} | {img} | {r['created_at']}")
    return "🖼 Pedidos de imagem\n\n" + "\n".join(lines)


def build_no_credits_message() -> str:
    lines = ["Você não possui créditos disponíveis 🙏\n", "Pacotes disponíveis:"]
    for pkg in CREDIT_PACKAGES:
        credits = pkg["credits"]
        price = pkg["price"]
        price_brl = f"R${price:.2f}".replace(".", ",")
        plural = "s" if credits > 1 else ""
        lines.append(f"• {credits} crédito{plural} → {price_brl}")
    lines.append("\nEscolha uma opção:")
    return "\n".join(lines)


def build_buy_credits_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for idx, pkg in enumerate(CREDIT_PACKAGES):
        credits = pkg["credits"]
        price = pkg["price"]
        price_brl = f"R${price:.2f}".replace(".", ",")
        plural = "s" if credits > 1 else ""
        label = f"{credits} crédito{plural} → {price_brl}"
        rows.append([InlineKeyboardButton(label, callback_data=f"{BUY_CREDITS_ACTION_PREFIX}{idx}")])
    return InlineKeyboardMarkup(rows)


def build_admin_credits_message(credits: list[dict]) -> str:
    if not credits:
        return "Nenhum saldo de crédito registrado."
    lines = [
        f"• {c['telegram_id']} — {c['credits_balance']} crédito(s) — {c['updated_at']}"
        for c in credits
    ]
    return "🎨 Saldos de crédito\n\n" + "\n".join(lines)


def build_journey_keyboard(
    journeys: list[object],
    cfg: Optional[TenantConfig] = None,
) -> Optional[InlineKeyboardMarkup]:
    _cfg = cfg or CURRENT_TENANT
    if not _cfg.feature_inline_actions:
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


def build_meuplano_message(info: dict) -> str:
    if not info.get("has_account"):
        return "Você ainda não possui uma conta. Use /start para começar."
    if not info.get("has_subscription"):
        return (
            "📋 Meu Plano\n\n"
            "❌ Você não possui assinatura ativa.\n\n"
            "Use /assinar para liberar seu acesso premium."
        )
    status = info.get("status", "")
    plan = info.get("plan", "")
    paid_until = info.get("paid_until")
    days = info.get("days_remaining")

    status_emoji = "✅" if status == "active" else "❌"
    status_label = "Ativa" if status == "active" else "Inativa"

    lines = ["📋 Meu Plano\n", f"{status_emoji} Status: {status_label}"]
    if plan:
        lines.append(f"📦 Plano: {plan}")
    if paid_until:
        lines.append(f"📅 Válido até: {paid_until}")
    if days is not None and status == "active":
        lines.append(f"⏳ {days} dias restantes")
    if status != "active":
        lines.append("\nUse /assinar para renovar seu acesso.")
    return "\n".join(lines)