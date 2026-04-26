import inspect
import logging
from typing import Any, Optional

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import Conflict, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.bot_flows import (
    cache_image_content,
    compute_content_id,
    get_cached_image_content,
    get_cached_reflection,
    remember_last_verse,
    resolve_last_verse,
    send_explanation_audio,
    send_explanation_flow,
    send_reflection_audio,
    send_reflection_flow,
    send_prayer_flow,
    send_verse_audio,
    send_verse_flow,
)
from app.session_state import JourneyState, SessionStore, register_session
from app.core.contracts import EngineInput
from app.core.engine.engine_facade import engine as _engine
from app.config import (
    APP_NAME,
    ASAAS_PAYMENT_LINK_URL,
    BOT_USERNAME,
    CREDIT_PACKAGES,
    CURRENT_TENANT,
    ENV,
    FEATURE_FAVORITES,
    FEATURE_JOURNEYS,
    IMAGE_PRICE,
    LOG_LEVEL,
    RATE_LIMIT_EXPLICAR,
    RATE_LIMIT_ORAR,
    RATE_LIMIT_VERSICULO,
    TELEGRAM_BOT_TOKEN,
    is_admin,
    is_production_environment,
    missing_settings,
)
from app.db import SessionLocal
from app.journey_service import (
    JOURNEYS,
    build_journey_catalog_message,
    get_active_journey,
    start_journey,
)
from app.observability import get_logger, log_event
from app.premium_experience import (
    ACTION_CONTINUE_JOURNEY,
    ACTION_EXPLAIN,
    ACTION_FAVORITE,
    ACTION_HEAR_EXPLANATION,
    ACTION_HEAR_VERSE,
    ACTION_NEW_VERSE,
    ACTION_PRAY,
    ACTION_SHOW_JOURNEYS,
    ACTION_CANCEL_IMAGE,
    BUY_CREDITS_ACTION_PREFIX,
    CONFIRM_IMAGE_ACTION_PREFIX,
    CREATE_IMAGE_ACTION_PREFIX,
    JOURNEY_ACTION_PREFIX,
    build_activation_error_message,
    build_activation_success_message,
    build_admin_credits_message,
    build_audio_unavailable_message,
    build_buy_credits_keyboard,
    build_favorite_added_message,
    build_favorite_exists_message,
    build_favorites_empty_message,
    build_favorites_message,
    build_help_message,
    build_image_confirm_keyboard,
    build_journey_keyboard,
    build_admin_image_requests_message,
    build_admin_status_message,
    build_admin_users_message,
    build_no_credits_message,
    build_no_history_message,
    build_prayer_actions_keyboard,
    build_rate_limit_message,
    build_reflection_actions_keyboard,
    build_reflection_unavailable_message,
    build_search_empty_message,
    build_search_results_message,
    build_payment_message,
    build_subscription_message,
    build_subscription_required_message,
    build_verse_actions_keyboard,
    build_verse_unavailable_message,
    build_welcome_message,
)
from app.rate_limiter import check_rate_limit
from app.credit_service import consume_credit, get_admin_credits, get_credits, refund_credit
from app.image_request_service import count_pending_requests, create_image_request, get_admin_image_requests
from app.payment_service import create_credit_payment, create_payment_for_user
from app.subscription_service import (
    activate_subscription_for_user,
    get_admin_recent_users,
    get_admin_stats,
    get_or_create_user,
    record_user_interaction,
    user_has_active_subscription,
)
from app.token_service import activate_subscription_via_token
from app.user_profile_service import (
    add_favorite_verse,
    get_user_explanation_depth,
    list_recent_favorites,
    record_theme_interest,
)
from app.verse_service import (
    format_verse_reference,
    format_verse_text,
    get_last_verse_for_user,
    search_verses_by_keyword,
)


logger = get_logger(__name__)

# Tracks (user_id:action) pairs currently in-flight to prevent double-click duplicate execution.
_IN_FLIGHT_ACTIONS: set[str] = set()


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def validate_bot_runtime() -> None:
    missing = missing_settings("TELEGRAM_BOT_TOKEN")
    if missing:
        raise RuntimeError(f"Configuração obrigatória ausente para o bot: {', '.join(missing)}")

    optional_missing = missing_settings("BOT_USERNAME")
    if optional_missing and is_production_environment():
        logger.warning("BOT_USERNAME ausente em produção; links de ativação podem ficar incompletos.")


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def get_message(update: Update):
    return update.effective_message


def get_user(update: Update):
    return update.effective_user


async def ensure_user_record(update: Update) -> None:
    user = get_user(update)
    if not user:
        return
    try:
        await maybe_await(
            get_or_create_user(
                telegram_user_id=str(user.id),
                telegram_username=user.username,
                full_name=user.full_name,
            )
        )
        await maybe_await(get_user_explanation_depth(SessionLocal, str(user.id)))
        await record_user_interaction(str(user.id))
    except Exception:
        logger.exception("Falha ao garantir registro do usuário.")

async def require_active_subscription(update: Update) -> bool:
    message = get_message(update)
    user = get_user(update)
    if not user or not message:
        return False

    await ensure_user_record(update)

    try:
        # 🔥 NOVO: buscar user completo do banco
        from app.subscription_service import get_or_create_user

        db_user = await maybe_await(
            get_or_create_user(
                telegram_user_id=str(user.id),
                telegram_username=user.username,
                full_name=user.full_name,
            )
        )

        # 🔥 BYPASS ADMIN
        if getattr(db_user, "role", "user") in ["admin", "super_admin"]:
            return True

        # 🔥 REGRA NORMAL
        if await maybe_await(user_has_active_subscription(str(user.id))):
            return True

    except Exception:
        logger.exception("Erro ao validar assinatura ativa.")

    await message.reply_text(build_subscription_required_message(ASAAS_PAYMENT_LINK_URL))
    return False

async def _check_rate_limit(update: Update, command: str, max_calls: int) -> bool:
    user = get_user(update)
    message = get_message(update)
    if not user or not message:
        return False
    if not check_rate_limit(f"{user.id}:{command}", max_calls=max_calls, window_seconds=3600):
        await message.reply_text(build_rate_limit_message())
        return False
    return True


# ── Handlers ─────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    await ensure_user_record(update)

    token = context.args[0].strip() if context.args else None
    if token:
        try:
            await activate_subscription_via_token(str(user.id), token)
            await message.reply_text(build_activation_success_message())
            return
        except Exception:
            logger.exception("Falha na ativação por token.")
            await message.reply_text(build_activation_error_message())
            return

    await message.reply_text(build_welcome_message())


async def ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if message:
        await message.reply_text(build_help_message())


async def assinar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    log_event(logger, "assinar_clicked", telegram_user_id=str(user.id))
    await ensure_user_record(update)
    await message.chat.send_action(ChatAction.TYPING)

    try:
        payment_info = await create_payment_for_user(
            telegram_user_id=str(user.id),
            full_name=user.full_name,
        )
    except Exception:
        logger.exception("Falha ao criar pagamento para usuário %s", user.id)
        payment_info = {"invoice_url": ASAAS_PAYMENT_LINK_URL, "pix_code": None, "value": None, "fallback": True}

    await message.reply_text(
        build_payment_message(
            invoice_url=payment_info["invoice_url"],
            pix_code=payment_info.get("pix_code"),
            value=payment_info.get("value"),
            fallback=payment_info.get("fallback", False),
        )
    )


async def meuultimo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    verse = await resolve_last_verse(update, context)
    if not message:
        return
    if not verse:
        await message.reply_text(build_no_history_message())
        return
    image_content_id = cache_image_content(context, "verse", str(verse.get("text", "")))
    await message.reply_text(
        format_verse_text(verse),
        reply_markup=build_verse_actions_keyboard(image_content_id=image_content_id),
    )


async def versiculo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return
    if not await require_active_subscription(update):
        return
    if not await _check_rate_limit(update, "versiculo", RATE_LIMIT_VERSICULO):
        return
    await message.chat.send_action(ChatAction.TYPING)
    try:
        await send_verse_flow(update, context)
    except TelegramError:
        logger.exception("Falha ao enviar versículo via Telegram.")
        await message.reply_text(build_verse_unavailable_message())
    except Exception:
        logger.exception("Falha geral ao enviar versículo.")
        await message.reply_text(build_verse_unavailable_message())


async def explicar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return
    if not await require_active_subscription(update):
        return
    if not await _check_rate_limit(update, "explicar", RATE_LIMIT_EXPLICAR):
        return
    await message.chat.send_action(ChatAction.TYPING)
    try:
        await send_explanation_flow(update, context)
    except TelegramError:
        logger.exception("Falha ao enviar explicação via Telegram.")
        await message.reply_text(build_audio_unavailable_message())
    except Exception:
        logger.exception("Falha ao gerar explicação.")
        await message.reply_text(build_reflection_unavailable_message())


async def reflexao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return
    if not await require_active_subscription(update):
        return
    if not await _check_rate_limit(update, "reflexao", RATE_LIMIT_EXPLICAR):
        return
    await message.chat.send_action(ChatAction.TYPING)
    try:
        await send_reflection_flow(update, context)
    except TelegramError:
        logger.exception("Falha ao enviar reflexão via Telegram.")
        await message.reply_text(build_audio_unavailable_message())
    except Exception:
        logger.exception("Falha ao gerar reflexão.")
        await message.reply_text(build_reflection_unavailable_message())


async def orar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return
    if not await require_active_subscription(update):
        return
    if not await _check_rate_limit(update, "orar", RATE_LIMIT_ORAR):
        return
    try:
        await send_prayer_flow(update, context)
    except Exception:
        logger.exception("Falha ao enviar oração premium.")
        await message.reply_text(build_no_history_message())


async def favoritar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user or not FEATURE_FAVORITES:
        return
    verse = await resolve_last_verse(update, context)
    if not verse:
        await message.reply_text(build_no_history_message())
        return
    added = await add_favorite_verse(SessionLocal, str(user.id), verse)
    reference = format_verse_reference(verse)
    await message.reply_text(
        build_favorite_added_message(reference) if added else build_favorite_exists_message(reference)
    )


async def favoritos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user or not FEATURE_FAVORITES:
        return
    items = await list_recent_favorites(SessionLocal, str(user.id))
    if not items:
        await message.reply_text(build_favorites_empty_message())
        return
    await message.reply_text(build_favorites_message(items))


async def trilhas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not FEATURE_JOURNEYS:
        return
    active = None
    if user:
        active = await get_active_journey(SessionLocal, str(user.id))
    await message.reply_text(
        build_journey_catalog_message(active.title if active else None),
        reply_markup=build_journey_keyboard(list(JOURNEYS.values())),
    )


async def _continuar_legacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback legado: lê estado direto do SessionStore(context) sem passar pelo engine."""
    message = get_message(update)
    verse = await resolve_last_verse(update, context)
    if not verse:
        if message:
            await message.reply_text(build_no_history_message())
        return
    state = SessionStore(context).get_state()
    if state is None or state == JourneyState.VERSE:
        await explicar(update, context)
    elif state == JourneyState.EXPLANATION:
        await reflexao(update, context)
    else:
        await orar(update, context)


async def continuar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Avança para o próximo passo espiritual consultando o engine para decisão de estado.

    O engine é a única fonte de verdade sobre "o que vem a seguir". O bot
    apenas recebe a instrução e despacha para o handler de renderização correto.
    Fallback ao legado se o engine não estiver disponível ou falhar.

    Nota: a verificação de assinatura NÃO está aqui — cada handler de destino
    (explicar, reflexao, orar) já a faz internamente. Duplicar causaria uma
    chamada de DB extra e quebraria testes que mockam apenas o handler final.
    """
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    # Fase 3: bridge — garante que context.user_data está no registry global
    # antes de chamar o engine, para que resolve_verse funcione via cache.
    register_session(CURRENT_TENANT.tenant_id, str(user.id), context.user_data)

    result = await _engine.execute(
        EngineInput(
            tenant_id=CURRENT_TENANT.tenant_id,
            user_id=str(user.id),
            action="continue_what",
        )
    )

    if not result.success:
        if result.error == "no_verse_history":
            await message.reply_text(build_no_history_message())
            return
        # Outro erro inesperado — cai no caminho legado com log
        log_event(logger, "continuar_engine_fallback", error=result.error, user_id=str(user.id))
        await _continuar_legacy(update, context)
        return

    next_action = result.data.get("next_action", "explanation_get")
    log_event(logger, "continuar_dispatched", next_action=next_action, user_id=str(user.id))

    # verse_select ocorre quando o engine encerrou o ciclo (state=PRAYER).
    # /continuar NÃO inicia novo ciclo — só /versiculo faz isso.
    # Por isso verse_select → orar (mantém comportamento original do comando).
    dispatch = {
        "explanation_get": explicar,
        "reflection_get": reflexao,
        "prayer_get": orar,
        "verse_select": orar,
    }
    handler = dispatch.get(next_action, explicar)
    await handler(update, context)


async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return
    if not await require_active_subscription(update):
        return
    keyword = " ".join(context.args).strip() if context.args else ""
    if not keyword:
        await message.reply_text("Use /buscar [tema] para encontrar um versículo. Exemplo: /buscar paz")
        return
    await message.chat.send_action(ChatAction.TYPING)
    try:
        results = await search_verses_by_keyword(keyword, limit=3)
        if not results:
            await message.reply_text(build_search_empty_message(keyword))
            return
        await message.reply_text(
            build_search_results_message(keyword, results),
            reply_markup=build_verse_actions_keyboard(),
        )
        log_event(logger, "buscar_sent", keyword=keyword, results=len(results))
    except Exception:
        logger.exception("Falha ao buscar versículos.")
        await message.reply_text(build_verse_unavailable_message())


async def meuplano(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return
    from app.subscription_service import get_subscription_info
    from app.premium_experience import build_meuplano_message
    try:
        info = await get_subscription_info(str(user.id))
        await message.reply_text(build_meuplano_message(info))
    except Exception:
        logger.exception("Falha ao consultar plano do usuário.")
        await message.reply_text("Não consegui consultar seu plano agora. Tente novamente em instantes.")


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    message = get_message(update)
    if not user or not message:
        return
    if not is_admin(str(user.id)):
        return
    subcommand = context.args[0].lower() if context.args else "status"
    try:
        if subcommand == "status":
            stats = await get_admin_stats()
            await message.reply_text(build_admin_status_message(stats))
        elif subcommand == "usuarios":
            users = await get_admin_recent_users()
            await message.reply_text(build_admin_users_message(users))
        elif subcommand == "pedidos":
            filter_arg = context.args[1].lower() if len(context.args) > 1 else None
            if filter_arg == "done":
                requests = await get_admin_image_requests(status="done")
            else:
                requests = await get_admin_image_requests(payment_status=filter_arg)
            await message.reply_text(build_admin_image_requests_message(requests))
        elif subcommand == "creditos":
            credits = await get_admin_credits()
            await message.reply_text(build_admin_credits_message(credits))
        else:
            await message.reply_text("Subcomandos disponíveis: status, usuarios, pedidos [pending_payment|paid|done], creditos")
    except Exception:
        logger.exception("Falha ao executar comando admin.")


async def _resolve_image_content(
    context: ContextTypes.DEFAULT_TYPE,
    content_type: str,
    content_id: str,
    telegram_user_id: str,
) -> Optional[tuple[str, str]]:
    """Context cache first; DB fallback for daily-job messages (no context available)."""
    cached = get_cached_image_content(context, content_id)
    if cached:
        return cached
    if content_type == "verse":
        verse = await get_last_verse_for_user(telegram_user_id)
        if verse:
            text = str(verse.get("text", ""))
            if compute_content_id(text) == content_id:
                return ("verse", text)
    return None


async def _handle_create_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, content_id: str
) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    cached = await _resolve_image_content(context, content_type, content_id, str(user.id))
    if not cached:
        await message.reply_text(
            "Não encontrei o conteúdo associado. Use /versiculo, /explicar, /reflexao ou /orar primeiro e clique em 🎨 Criar imagem."
        )
        return

    balance = await get_credits(str(user.id))
    if balance < 1:
        await message.reply_text(
            build_no_credits_message(),
            reply_markup=build_buy_credits_keyboard(),
        )
        return

    await message.reply_text(
        "Posso transformar essa mensagem em uma imagem especial 🙏\n\n"
        "Custo: 1 crédito\n\n"
        "Deseja continuar?",
        reply_markup=build_image_confirm_keyboard(content_type, content_id),
    )


async def _handle_confirm_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE, content_type: str, content_id: str
) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    cached = await _resolve_image_content(context, content_type, content_id, str(user.id))
    if not cached:
        await message.reply_text(
            "Não foi possível registrar seu pedido. O conteúdo não está mais disponível. Tente novamente."
        )
        return

    pending_count = await count_pending_requests(str(user.id))
    if pending_count >= 5:
        await message.reply_text(
            "Você já possui pedidos em andamento 🙏\n"
            "Aguarde a finalização antes de solicitar novos."
        )
        return

    credit_ok = await consume_credit(str(user.id))
    if not credit_ok:
        await message.reply_text(
            "Você não possui créditos suficientes 🙏\n\n"
            "Use o botão 🎨 Criar imagem para ver os pacotes disponíveis.",
        )
        return

    _, text = cached
    try:
        await create_image_request(
            SessionLocal,
            telegram_id=str(user.id),
            content_type=content_type,
            content_text=text,
            price=IMAGE_PRICE,
            payment_status="paid",
        )
    except Exception:
        logger.exception("Falha ao registrar pedido de imagem após consumo de crédito.")
        await refund_credit(str(user.id))
        await message.reply_text("Não consegui registrar seu pedido. Tente novamente em instantes.")
        return

    log_event(
        logger,
        "image_request_confirmed",
        telegram_user_id=str(user.id),
        content_type=content_type,
        content_id=content_id,
    )
    await message.reply_text(
        "Seu pedido foi recebido 🙏\n\n"
        "Sua imagem será preparada e enviada em instantes."
    )


async def _handle_buy_credits(
    update: Update, context: ContextTypes.DEFAULT_TYPE, package_idx: int
) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    if package_idx < 0 or package_idx >= len(CREDIT_PACKAGES):
        await message.reply_text("Pacote inválido. Por favor, tente novamente.")
        return

    pkg = CREDIT_PACKAGES[package_idx]
    credits = pkg["credits"]
    price = pkg["price"]
    full_name = getattr(user, "full_name", None)

    result = await create_credit_payment(
        telegram_user_id=str(user.id),
        credits=credits,
        price=price,
        full_name=full_name,
    )

    credits_label = f"{credits} crédito{'s' if credits > 1 else ''}"

    if result["fallback"] or not result.get("invoice_url"):
        price_brl = f"R${price:.2f}".replace(".", ",")
        await message.reply_text(
            f"Não foi possível gerar o pagamento agora.\n\n"
            f"Entre em contato para adquirir {credits_label} por {price_brl}."
        )
        return

    invoice_url = result["invoice_url"]
    pix_code = result.get("pix_code")

    if pix_code:
        text = (
            f"Para continuar, finalize seu pagamento:\n\n"
            f"{invoice_url}\n\n"
            f"Ou use o código PIX (copia e cola):\n\n"
            f"`{pix_code}`\n\n"
            f"Assim que confirmado, seus {credits_label} serão adicionados automaticamente."
        )
    else:
        text = (
            f"Para continuar, finalize seu pagamento:\n\n"
            f"{invoice_url}\n\n"
            f"Assim que confirmado, seus {credits_label} serão adicionados automaticamente."
        )

    await message.reply_text(text)


async def handle_interactive_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    user = get_user(update)

    # confirm_image: DB write — lock against double-click and give feedback immediately.
    if data.startswith(CONFIRM_IMAGE_ACTION_PREFIX):
        rest = data[len(CONFIRM_IMAGE_ACTION_PREFIX):]
        parts = rest.split("|", 1)
        if len(parts) != 2:
            await query.answer()
            return
        content_type, content_id = parts
        if user:
            lock_key = f"{user.id}:{data}"
            if lock_key in _IN_FLIGHT_ACTIONS:
                await query.answer("⏳ Seu pedido já está sendo processado.", show_alert=False)
                return
            _IN_FLIGHT_ACTIONS.add(lock_key)
        await query.answer("⏳ Registrando pedido...", show_alert=False)
        try:
            await _handle_confirm_image(update, context, content_type, content_id)
        finally:
            if user:
                _IN_FLIGHT_ACTIONS.discard(f"{user.id}:{data}")
        return

    # Slow content-generation actions: give immediate visual feedback and block double-click.
    # query.answer() MUST be called within 10s — we call it here for all branches.
    if data in {ACTION_EXPLAIN, ACTION_CONTINUE_JOURNEY, ACTION_PRAY}:
        if user:
            lock_key = f"{user.id}:{data}"
            if lock_key in _IN_FLIGHT_ACTIONS:
                await query.answer("⏳ Sua solicitação já está sendo processada.", show_alert=False)
                return
            _IN_FLIGHT_ACTIONS.add(lock_key)
        await query.answer("⏳ Gerando...", show_alert=False)
        try:
            if data == ACTION_EXPLAIN:
                await explicar(update, context)
            elif data == ACTION_CONTINUE_JOURNEY:
                await continuar(update, context)
            else:
                await orar(update, context)
        finally:
            if user:
                _IN_FLIGHT_ACTIONS.discard(f"{user.id}:{data}")
        return

    await query.answer()
    if data.startswith(BUY_CREDITS_ACTION_PREFIX):
        rest = data[len(BUY_CREDITS_ACTION_PREFIX):]
        try:
            pkg_idx = int(rest)
        except ValueError:
            return
        await _handle_buy_credits(update, context, pkg_idx)
        return
    if data.startswith(CREATE_IMAGE_ACTION_PREFIX):
        rest = data[len(CREATE_IMAGE_ACTION_PREFIX):]
        parts = rest.split("|", 1)
        if len(parts) != 2:
            return
        content_type, content_id = parts
        await _handle_create_image(update, context, content_type, content_id)
        return
    if data == ACTION_CANCEL_IMAGE:
        return
    if data == ACTION_HEAR_VERSE:
        verse = await resolve_last_verse(update, context)
        message = get_message(update)
        if verse and message:
            await send_verse_audio(message, verse)
        return
    if data == ACTION_FAVORITE:
        await favoritar(update, context)
        return
    if data == ACTION_NEW_VERSE:
        await versiculo(update, context)
        return
    if data == ACTION_HEAR_EXPLANATION:
        message = get_message(update)
        verse = await resolve_last_verse(update, context)
        reflection = get_cached_reflection(context)
        if message and verse and reflection:
            if reflection.depth == "deep":
                await send_reflection_audio(message, verse, reflection)
            else:
                await send_explanation_audio(message, verse, reflection)
        else:
            await explicar(update, context)
        return
    if data == ACTION_SHOW_JOURNEYS:
        await trilhas(update, context)
        return
    if data.startswith(JOURNEY_ACTION_PREFIX):
        journey_key = data.split(":", 1)[1]
        if not user:
            return
        journey = await start_journey(SessionLocal, str(user.id), journey_key)
        if not journey:
            return
        await record_theme_interest(SessionLocal, str(user.id), journey.key, source="journey")
        message = get_message(update)
        if message:
            await message.reply_text(
                f"🛤️ Trilha iniciada: {journey.title}\n\n{journey.summary}\n\nUse /versiculo para viver o próximo passo com esta intenção no coração.",
                reply_markup=build_verse_actions_keyboard(),
            )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if isinstance(error, Conflict):
        logger.error("Conflito do Telegram: existe outra instância do bot rodando.")
        return
    logger.exception("Erro não tratado no bot.", exc_info=error)


async def _init_services() -> None:
    """Inicializa serviços assíncronos do bot (session backend, etc.)."""
    from app.core.session.factory import init_session_backend
    await init_session_backend()


def main() -> None:
    import asyncio
    setup_logging()
    validate_bot_runtime()

    asyncio.get_event_loop().run_until_complete(_init_services())

    log_event(logger, "bot_starting", app_name=APP_NAME, env=ENV, bot_username=BOT_USERNAME)
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ajuda", ajuda))
    application.add_handler(CommandHandler(["versiculo"], versiculo))
    application.add_handler(CommandHandler("explicar", explicar))
    application.add_handler(CommandHandler(["reflexao"], reflexao))
    application.add_handler(CommandHandler("orar", orar))
    application.add_handler(CommandHandler("meuultimo", meuultimo))
    application.add_handler(CommandHandler("assinar", assinar))
    application.add_handler(CommandHandler("meuplano", meuplano))
    application.add_handler(CommandHandler("favoritar", favoritar))
    application.add_handler(CommandHandler("favoritos", favoritos))
    application.add_handler(CommandHandler("trilhas", trilhas))
    application.add_handler(CommandHandler("continuar", continuar))
    application.add_handler(CommandHandler("buscar", buscar))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CallbackQueryHandler(handle_interactive_action, pattern=r"^(action:|journey:).*"))
    application.add_error_handler(on_error)

    log_event(logger, "bot_started", bot_username=BOT_USERNAME)

    try:
        application.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error("Não foi possível iniciar: já existe outra instância consumindo updates deste bot.")
    except TelegramError:
        logger.exception("Falha do Telegram ao iniciar o bot.")
    finally:
        log_event(logger, "bot_stopped", app_name=APP_NAME, env=ENV, bot_username=BOT_USERNAME)


if __name__ == "__main__":
    main()
