import inspect
import logging
from typing import Any

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.error import Conflict, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.audio_service import AudioAsset, ensure_named_audio_asset
from app.config import (
    ASAAS_PAYMENT_LINK_URL,
    BOT_USERNAME,
    FEATURE_FAVORITES,
    FEATURE_JOURNEYS,
    LOG_LEVEL,
    TELEGRAM_BOT_TOKEN,
)
from app.content_service import (
    ReflectionContent,
    build_default_prayer,
    build_explanation_audio_text,
    generate_reflection_content,
    render_prayer_message,
    render_reflection_message,
)
from app.db import SessionLocal
from app.journey_service import (
    JOURNEYS,
    build_active_journey_touchpoint,
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
    JOURNEY_ACTION_PREFIX,
    build_activation_error_message,
    build_activation_success_message,
    build_audio_unavailable_message,
    build_favorite_added_message,
    build_favorite_exists_message,
    build_favorites_empty_message,
    build_favorites_message,
    build_help_message,
    build_journey_keyboard,
    build_no_history_message,
    build_prayer_actions_keyboard,
    build_prayer_unavailable_message,
    build_reflection_actions_keyboard,
    build_reflection_unavailable_message,
    build_subscription_message,
    build_subscription_required_message,
    build_verse_actions_keyboard,
    build_verse_unavailable_message,
    build_welcome_message,
)
from app.subscription_service import (
    activate_subscription_for_user,
    get_or_create_user,
    user_has_active_subscription,
)
from app.token_service import consume_activation_token, validate_activation_token
from app.user_profile_service import (
    add_favorite_verse,
    get_user_explanation_depth,
    list_recent_favorites,
    record_theme_interest,
)
from app.verse_service import (
    build_tts_text,
    format_verse_reference,
    format_verse_text,
    get_last_verse_for_user,
    get_random_verse_for_user,
    save_verse_history,
)


logger = get_logger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def get_message(update: Update):
    return update.effective_message


def get_user(update: Update):
    return update.effective_user


def remember_last_verse(context: ContextTypes.DEFAULT_TYPE, verse: dict[str, Any]) -> None:
    context.user_data["last_verse"] = verse


def remember_last_reflection(context: ContextTypes.DEFAULT_TYPE, reflection: ReflectionContent) -> None:
    context.user_data["last_reflection"] = reflection.as_dict()


def get_cached_reflection(context: ContextTypes.DEFAULT_TYPE) -> ReflectionContent | None:
    payload = context.user_data.get("last_reflection")
    if not isinstance(payload, dict):
        return None
    return ReflectionContent.from_dict(payload)


async def resolve_last_verse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    cached = context.user_data.get("last_verse")
    if isinstance(cached, dict):
        return cached

    user = get_user(update)
    if not user:
        return None

    verse = await get_last_verse_for_user(str(user.id))
    if verse:
        remember_last_verse(context, verse)
    return verse


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
    except Exception:
        logger.exception("Falha ao garantir registro do usuário.")


async def require_active_subscription(update: Update) -> bool:
    message = get_message(update)
    user = get_user(update)

    if not user or not message:
        return False

    await ensure_user_record(update)

    try:
        if await maybe_await(user_has_active_subscription(str(user.id))):
            return True
    except Exception:
        logger.exception("Erro ao validar assinatura ativa.")

    await message.reply_text(build_subscription_required_message())
    return False


async def activate_user_from_token(user_id: str, token: str) -> None:
    validated = await maybe_await(validate_activation_token(token))
    if not validated:
        raise ValueError("Token inválido ou expirado.")

    consumed = await maybe_await(consume_activation_token(token, user_id))
    if not consumed:
        raise ValueError("Não foi possível consumir o token de ativação.")

    await maybe_await(activate_subscription_for_user(telegram_user_id=user_id))


async def send_audio_asset(
    message,
    asset: AudioAsset,
    *,
    title: str,
    performer: str,
    caption: str | None = None,
) -> None:
    with asset.path.open("rb") as file_handle:
        telegram_file = InputFile(file_handle, filename=asset.path.name)
        await message.reply_audio(
            audio=telegram_file,
            title=title,
            performer=performer,
            caption=caption,
        )


async def send_verse_audio(message, verse: dict[str, Any]) -> None:
    asset = await ensure_named_audio_asset("versiculo", verse, build_tts_text(verse))
    await send_audio_asset(
        message,
        asset,
        title=f"Áudio de {format_verse_reference(verse)}",
        performer="Profeta",
    )
    log_event(
        logger,
        "verse_audio_sent",
        verse_reference=format_verse_reference(verse),
        cache_hit=asset.cache_hit,
    )


async def send_reflection_audio(message, verse: dict[str, Any], reflection: ReflectionContent) -> None:
    asset = await ensure_named_audio_asset(
        "explicacao",
        verse,
        build_explanation_audio_text(verse, reflection),
    )
    await send_audio_asset(
        message,
        asset,
        title=f"Reflexão de {format_verse_reference(verse)}",
        performer="Profeta",
    )
    log_event(
        logger,
        "reflection_audio_sent",
        verse_reference=format_verse_reference(verse),
        cache_hit=asset.cache_hit,
    )


async def send_verse_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    verse: dict[str, Any] | None = None,
) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    verse = verse or await get_random_verse_for_user(str(user.id))
    if not verse:
        await message.reply_text(build_verse_unavailable_message())
        return

    active_journey = None
    if FEATURE_JOURNEYS:
        active_journey = await get_active_journey(SessionLocal, str(user.id))

    await save_verse_history(str(user.id), verse)
    remember_last_verse(context, verse)

    await message.reply_text(
        format_verse_text(verse, active_journey.title if active_journey else None),
        reply_markup=build_verse_actions_keyboard(),
    )
    log_event(
        logger,
        "verse_sent",
        telegram_user_id=user.id,
        verse_reference=format_verse_reference(verse),
        journey=active_journey.key if active_journey else "",
    )

    await send_verse_audio(message, verse)


async def send_reflection_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    verse = await resolve_last_verse(update, context)
    if not verse:
        await message.reply_text(build_no_history_message())
        return

    depth = await get_user_explanation_depth(SessionLocal, str(user.id))
    active_journey = None
    if FEATURE_JOURNEYS:
        active_journey = await get_active_journey(SessionLocal, str(user.id))

    reflection = await generate_reflection_content(
        verse,
        depth=depth,
        journey_title=active_journey.title if active_journey else None,
    )
    remember_last_reflection(context, reflection)

    await message.reply_text(
        render_reflection_message(verse, reflection, active_journey.title if active_journey else None),
        reply_markup=build_reflection_actions_keyboard(),
    )
    log_event(
        logger,
        "reflection_sent",
        telegram_user_id=user.id,
        verse_reference=format_verse_reference(verse),
        depth=depth,
    )

    await send_reflection_audio(message, verse, reflection)


async def send_prayer_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    verse = await resolve_last_verse(update, context)
    if not verse:
        await message.reply_text(build_prayer_unavailable_message())
        return

    reflection = get_cached_reflection(context)
    prayer = reflection.prayer if reflection and reflection.prayer else build_default_prayer(verse)

    active_journey = None
    if FEATURE_JOURNEYS:
        active_journey = await get_active_journey(SessionLocal, str(user.id))

    await message.reply_text(
        render_prayer_message(verse, prayer, active_journey.title if active_journey else None),
        reply_markup=build_prayer_actions_keyboard(),
    )
    log_event(
        logger,
        "prayer_sent",
        telegram_user_id=user.id,
        verse_reference=format_verse_reference(verse),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user:
        return

    await ensure_user_record(update)

    token = context.args[0].strip() if context.args else None
    if token:
        try:
            await activate_user_from_token(str(user.id), token)
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
    if message:
        await message.reply_text(build_subscription_message(ASAAS_PAYMENT_LINK_URL))


async def meuultimo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    verse = await resolve_last_verse(update, context)
    if not message:
        return

    if not verse:
        await message.reply_text(build_no_history_message())
        return

    await message.reply_text(format_verse_text(verse), reply_markup=build_verse_actions_keyboard())


async def versiculo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return

    if not await require_active_subscription(update):
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

    await message.chat.send_action(ChatAction.TYPING)
    try:
        await send_reflection_flow(update, context)
    except TelegramError:
        logger.exception("Falha ao enviar reflexão via Telegram.")
        await message.reply_text(build_audio_unavailable_message())
    except Exception:
        logger.exception("Falha ao gerar reflexão premium.")
        await message.reply_text(build_reflection_unavailable_message())


async def orar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    if not message:
        return

    if not await require_active_subscription(update):
        return

    try:
        await send_prayer_flow(update, context)
    except Exception:
        logger.exception("Falha ao enviar oração premium.")
        await message.reply_text(build_prayer_unavailable_message())


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


async def continuar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = get_message(update)
    user = get_user(update)
    if not message or not user or not FEATURE_JOURNEYS:
        return

    touchpoint = await build_active_journey_touchpoint(SessionLocal, str(user.id))
    if not touchpoint:
        await trilhas(update, context)
        return

    await message.reply_text(touchpoint, reply_markup=build_verse_actions_keyboard())


async def handle_interactive_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = query.data or ""
    user = get_user(update)

    if data == ACTION_EXPLAIN:
        await explicar(update, context)
        return
    if data == ACTION_HEAR_VERSE:
        verse = await resolve_last_verse(update, context)
        message = get_message(update)
        if verse and message:
            await send_verse_audio(message, verse)
        return
    if data == ACTION_PRAY:
        await orar(update, context)
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
            await send_reflection_audio(message, verse, reflection)
        else:
            await explicar(update, context)
        return
    if data == ACTION_SHOW_JOURNEYS:
        await trilhas(update, context)
        return
    if data == ACTION_CONTINUE_JOURNEY:
        await continuar(update, context)
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


def main() -> None:
    setup_logging()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ajuda", ajuda))
    application.add_handler(CommandHandler("versiculo", versiculo))
    application.add_handler(CommandHandler("explicar", explicar))
    application.add_handler(CommandHandler("orar", orar))
    application.add_handler(CommandHandler("meuultimo", meuultimo))
    application.add_handler(CommandHandler("assinar", assinar))
    application.add_handler(CommandHandler("favoritar", favoritar))
    application.add_handler(CommandHandler("favoritos", favoritos))
    application.add_handler(CommandHandler("trilhas", trilhas))
    application.add_handler(CommandHandler("continuar", continuar))
    application.add_handler(CallbackQueryHandler(handle_interactive_action, pattern=r"^(action:|journey:).*"))
    application.add_error_handler(on_error)

    log_event(logger, "bot_started", bot_username=BOT_USERNAME)

    try:
        application.run_polling(drop_pending_updates=True)
    except Conflict:
        logger.error("Não foi possível iniciar: já existe outra instância consumindo updates deste bot.")
    except TelegramError:
        logger.exception("Falha do Telegram ao iniciar o bot.")


if __name__ == "__main__":
    main()
