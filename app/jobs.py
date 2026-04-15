import asyncio
import json
import logging
import random
from pathlib import Path

from telegram import Bot
from sqlalchemy import select

from app.config import TELEGRAM_BOT_TOKEN
from app.db import SessionLocal
from app.models import User, Subscription, VerseHistory
from app.audio_service import generate_tts_audio
from app.observability import log_event

BIBLE_PATH = Path("data/bible/bible.json")
LOGS_DIR = Path("logs")
LOG_FILE = LOGS_DIR / "daily_job.log"
RECENT_HISTORY_LIMIT = 5


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("daily_job")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        file_handler = logging.FileHandler(LOG_FILE)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def load_verses() -> list[dict]:
    if not BIBLE_PATH.exists():
        return []

    with open(BIBLE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

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
    available = [v for v in verses if v.get("text") not in recent_texts]

    if not available:
        logger.warning("Todos os versículos recentes foram bloqueados. Aplicando fallback.")
        return random.choice(verses)

    return random.choice(available)


async def save_verse_history(user_id: str, verse: dict) -> None:
    async with SessionLocal() as session:
        row = VerseHistory(
            telegram_user_id=str(user_id),
            book=str(verse.get("book", "")),
            chapter=str(verse.get("chapter", "")),
            verse=str(verse.get("verse", "")),
            text=str(verse.get("text", "")),
        )
        session.add(row)
        await session.commit()


async def main() -> None:
    logger = setup_logger()
    logger.info("===== INÍCIO DO JOB DIÁRIO =====")
    log_event(logger, "daily_job_started")

    verses = load_verses()
    if not verses:
        logger.error("Nenhum versículo encontrado.")
        return

    user_ids = await get_active_user_ids()
    logger.info(f"Usuários ativos encontrados: {len(user_ids)}")
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
                f"📖 Versículo do dia\n\n"
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

            # TEXTO PARA ÁUDIO
            tts_text = (
                f"{verse['book']}. "
                f"capítulo {verse['chapter']}. "
                f"versículo {verse['verse']}. "
                f"{verse['text']}"
            )

            # GERA OU USA CACHE
            audio_path = await generate_tts_audio(tts_text)

            # ENVIA TEXTO
            await bot.send_message(chat_id=user_id, text=message)

            # ENVIA ÁUDIO
            with open(audio_path, "rb") as audio:
                await bot.send_audio(
                    chat_id=user_id,
                    audio=audio,
                    title="Versículo do dia",
                )

            await save_verse_history(user_id, verse)

            logger.info(
                f"Envio concluído | telegram_user_id={user_id} | verse={verse['book']} {verse['chapter']}:{verse['verse']}"
            )
            log_event(
                logger,
                "daily_verse_send_completed",
                telegram_user_id=user_id,
                verse_reference=f"{verse['book']} {verse['chapter']}:{verse['verse']}",
            )

            sent += 1

        except Exception as e:
            logger.error(f"Erro ao enviar para {user_id}: {e}")
            log_event(logger, "daily_verse_send_failed", level=logging.ERROR, telegram_user_id=user_id, error=str(e))
            failed += 1

    logger.info(f"===== FIM DO JOB DIÁRIO | enviados={sent} | falhas={failed} =====")
    log_event(logger, "daily_job_finished", sent=sent, failed=failed)


if __name__ == "__main__":
    asyncio.run(main())
