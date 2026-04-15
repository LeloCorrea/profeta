import asyncio
import json
import logging
import random
from pathlib import Path

from sqlalchemy import select
from telegram import Bot

from app.audio_service import ensure_named_audio_asset
from app.config import APP_NAME, ENV, TELEGRAM_BOT_TOKEN, missing_settings
from app.db import SessionLocal
from app.models import Subscription, User, VerseHistory
from app.observability import log_event
from app.verse_service import build_tts_text

BIBLE_PATH = Path("data/bible/bible.json")
LOGS_DIR = Path("logs")
LOG_FILE = LOGS_DIR / "daily_job.log"
RECENT_HISTORY_LIMIT = 5


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("daily_job")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        file_handler = logging.FileHandler(LOG_FILE)
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def load_verses() -> list[dict]:
    if not BIBLE_PATH.exists():
        return []

    with open(BIBLE_PATH, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    if not isinstance(data, list):
        return []

    return data


async def get_active_user_ids() -> list[str]:
    async with SessionLocal() as session:
        stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]


async def get_recent_verses(user_id: str) -> list[str]:
    async with SessionLocal() as session:
        stmt = (
            select(VerseHistory.text)
            .where(VerseHistory.telegram_user_id == str(user_id))
            .order_by(VerseHistory.id.desc())
            .limit(RECENT_HISTORY_LIMIT)
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]


def pick_non_repeating_verse(verses: list[dict], recent_texts: list[str], logger):
    available = [verse for verse in verses if verse.get("text") not in recent_texts]

    if not available:
        logger.warning("Todos os versiculos recentes foram bloqueados. Aplicando fallback.")
        return random.choice(verses)

    return random.choice(available)


async def save_verse_history(user_id: str, verse: dict) -> None:
    async with SessionLocal() as session:
        session.add(
            VerseHistory(
                telegram_user_id=str(user_id),
                book=str(verse.get("book", "")),
                chapter=str(verse.get("chapter", "")),
                verse=str(verse.get("verse", "")),
                text=str(verse.get("text", "")),
            )
        )
        await session.commit()


async def main() -> None:
    logger = setup_logger()
    missing = missing_settings("TELEGRAM_BOT_TOKEN")
    if missing:
        log_event(
            logger,
            "daily_job_configuration_error",
            level=logging.ERROR,
            missing_settings=", ".join(missing),
        )
        raise RuntimeError(f"Configuracao obrigatoria ausente para job diario: {', '.join(missing)}")

    logger.info("===== INICIO DO JOB DIARIO =====")
    log_event(logger, "daily_job_started", app_name=APP_NAME, env=ENV)

    verses = load_verses()
    if not verses:
        logger.error("Nenhum versiculo encontrado.")
        log_event(logger, "daily_job_aborted", level=logging.ERROR, reason="no_verses_available")
        return

    user_ids = await get_active_user_ids()
    logger.info(f"Usuarios ativos encontrados: {len(user_ids)}")
    log_event(logger, "daily_job_active_users_loaded", active_user_count=len(user_ids))

    if not user_ids:
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    sent = 0
    failed = 0

    for user_id in user_ids:
        try:
            recent = await get_recent_verses(user_id)
            verse = pick_non_repeating_verse(verses, recent, logger)

            message = (
                "📖 Versículo do dia\n\n"
                f"{verse['book']} {verse['chapter']}:{verse['verse']}\n\n"
                f"\"{verse['text']}\""
            )

            logger.info(
                f"Preparando envio | telegram_user_id={user_id} | verse={verse['book']} {verse['chapter']}:{verse['verse']}"
            )
            log_event(
                logger,
                "daily_verse_send_started",
                telegram_user_id=user_id,
                verse_reference=f"{verse['book']} {verse['chapter']}:{verse['verse']}",
            )

            audio_asset = await ensure_named_audio_asset(
                "versiculo",
                verse,
                build_tts_text(verse),
            )

            await bot.send_message(chat_id=user_id, text=message)

            with open(audio_asset.path, "rb") as audio:
                await bot.send_audio(
                    chat_id=user_id,
                    audio=audio,
                    title="Versiculo do dia",
                )

            await save_verse_history(user_id, verse)

            logger.info(
                f"Envio concluido | telegram_user_id={user_id} | verse={verse['book']} {verse['chapter']}:{verse['verse']}"
            )
            log_event(
                logger,
                "daily_verse_send_completed",
                telegram_user_id=user_id,
                verse_reference=f"{verse['book']} {verse['chapter']}:{verse['verse']}",
            )

            sent += 1

        except Exception as error:
            logger.error(f"Erro ao enviar para {user_id}: {error}")
            log_event(
                logger,
                "daily_verse_send_failed",
                level=logging.ERROR,
                telegram_user_id=user_id,
                error=str(error),
            )
            failed += 1

    logger.info(f"===== FIM DO JOB DIARIO | enviados={sent} | falhas={failed} =====")
    log_event(logger, "daily_job_finished", sent=sent, failed=failed)


if __name__ == "__main__":
    asyncio.run(main())
