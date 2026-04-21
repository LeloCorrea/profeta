from typing import Any, Optional

from telegram import InputFile, Update
from telegram.ext import ContextTypes

from app.audio_service import AudioAsset, ensure_named_audio_asset
from app.config import FEATURE_JOURNEYS
from app.content_service import (
    ReflectionContent,
    build_default_prayer,
    build_explanation_audio_text,
    get_or_create_reflection_content,
    render_prayer_message,
    render_reflection_message,
)
from app.db import SessionLocal
from app.journey_service import get_active_journey
from app.observability import get_logger, log_event
from app.premium_experience import (
    build_no_history_message,
    build_prayer_actions_keyboard,
    build_prayer_unavailable_message,
    build_reflection_actions_keyboard,
    build_verse_actions_keyboard,
    build_verse_unavailable_message,
)
from app.user_profile_service import get_user_explanation_depth
from app.verse_service import (
    build_tts_text,
    format_verse_reference,
    format_verse_text,
    get_last_verse_for_user,
    get_random_verse_for_user,
    save_verse_history,
)

logger = get_logger(__name__)


def remember_last_verse(context: ContextTypes.DEFAULT_TYPE, verse: dict[str, Any]) -> None:
    context.user_data["last_verse"] = verse


def remember_last_reflection(context: ContextTypes.DEFAULT_TYPE, reflection: ReflectionContent) -> None:
    context.user_data["last_reflection"] = reflection.as_dict()


def get_cached_reflection(context: ContextTypes.DEFAULT_TYPE) -> Optional[ReflectionContent]:
    payload = context.user_data.get("last_reflection")
    if not isinstance(payload, dict):
        return None
    return ReflectionContent.from_dict(payload)


async def resolve_last_verse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[dict[str, Any]]:
    cached = context.user_data.get("last_verse")
    if isinstance(cached, dict):
        return cached
    user = update.effective_user
    if not user:
        return None
    verse = await get_last_verse_for_user(str(user.id))
    if verse:
        remember_last_verse(context, verse)
    return verse


async def send_audio_asset(
    message,
    asset: AudioAsset,
    *,
    title: str,
    performer: str,
    caption: Optional[str] = None,
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
    audio_text = build_explanation_audio_text(verse, reflection)
    if not audio_text:
        logger.info("[Áudio] Reflexão fallback — áudio omitido para %s", format_verse_reference(verse))
        return
    asset = await ensure_named_audio_asset("explicacao", verse, audio_text)
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
    verse: Optional[dict[str, Any]] = None,
) -> None:
    message = update.effective_message
    user = update.effective_user
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


async def send_reflection_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    depth_override: Optional[str] = None,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    verse = await resolve_last_verse(update, context)
    if not verse:
        await message.reply_text(build_no_history_message())
        return

    depth = depth_override or await get_user_explanation_depth(SessionLocal, str(user.id))
    active_journey = None
    if FEATURE_JOURNEYS:
        active_journey = await get_active_journey(SessionLocal, str(user.id))

    reflection = await get_or_create_reflection_content(
        SessionLocal,
        str(user.id),
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
    message = update.effective_message
    user = update.effective_user
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
