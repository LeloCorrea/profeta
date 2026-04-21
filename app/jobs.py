import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from telegram import Bot
from telegram.error import TelegramError

from app.audio_service import cleanup_old_audio_files, ensure_named_audio_asset
from app.config import APP_NAME, AUDIO_MAX_AGE_DAYS, ENV, TELEGRAM_BOT_TOKEN, missing_settings
from app.db import SessionLocal
from app.models import Subscription, User
from app.observability import log_event
from app.subscription_service import expire_overdue_subscriptions
from app.verse_service import build_tts_text, get_random_verse_for_user, save_verse_history

_FATAL_TELEGRAM_KEYWORDS = ("blocked", "bot was blocked", "deactivated", "kicked", "chat not found", "user not found")

LOGS_DIR = Path("logs")
LOG_FILE = LOGS_DIR / "daily_job.log"


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


async def get_active_user_ids() -> list[str]:
    async with SessionLocal() as session:
        stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]


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

    expired = await expire_overdue_subscriptions()
    if expired:
        logger.info(f"Assinaturas expiradas desativadas: {expired}")

    cleaned = cleanup_old_audio_files(max_age_days=AUDIO_MAX_AGE_DAYS)
    if cleaned:
        logger.info(f"Arquivos de áudio antigos removidos: {cleaned}")

    user_ids = await get_active_user_ids()
    logger.info(f"Usuarios ativos encontrados: {len(user_ids)}")
    log_event(logger, "daily_job_active_users_loaded", active_user_count=len(user_ids))

    if not user_ids:
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    sent = 0
    failed = 0

    for user_id in user_ids:
        success = await _send_verse_with_retry(user_id, bot, logger)
        if success:
            sent += 1
        else:
            failed += 1

    logger.info(f"===== FIM DO JOB DIARIO | enviados={sent} | falhas={failed} =====")
    log_event(logger, "daily_job_finished", sent=sent, failed=failed)


async def _send_verse_with_retry(user_id: str, bot: Bot, logger: logging.Logger, max_attempts: int = 3) -> bool:
    for attempt in range(max_attempts):
        try:
            verse = await get_random_verse_for_user(user_id)
            if not verse:
                logger.error(f"Nenhum versículo disponível para {user_id}")
                return False

            verse_ref = f"{verse['book']} {verse['chapter']}:{verse['verse']}"
            message_text = f"📖 Versículo do dia\n\n{verse_ref}\n\n\"{verse['text']}\""

            log_event(logger, "daily_verse_send_started", telegram_user_id=user_id, verse_reference=verse_ref)

            audio_asset = await ensure_named_audio_asset("versiculo", verse, build_tts_text(verse))

            await bot.send_message(chat_id=user_id, text=message_text)
            with open(audio_asset.path, "rb") as audio:
                await bot.send_audio(chat_id=user_id, audio=audio, title="Versículo do dia")

            await save_verse_history(user_id, verse)
            log_event(logger, "daily_verse_send_completed", telegram_user_id=user_id, verse_reference=verse_ref)
            return True

        except TelegramError as error:
            err_lower = str(error).lower()
            if any(keyword in err_lower for keyword in _FATAL_TELEGRAM_KEYWORDS):
                logger.warning(f"Usuário {user_id} inacessível (fatal): {error}")
                log_event(logger, "daily_verse_send_failed", level=logging.WARNING, telegram_user_id=user_id, error=str(error), fatal=True)
                return False
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(f"Tentativa {attempt + 1} falhou para {user_id}, retry em {wait}s: {error}")
                await asyncio.sleep(wait)
            else:
                logger.error(f"Falha definitiva após {max_attempts} tentativas para {user_id}: {error}")
                log_event(logger, "daily_verse_send_failed", level=logging.ERROR, telegram_user_id=user_id, error=str(error), attempts=max_attempts)
                return False

        except Exception as error:
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(f"Erro inesperado na tentativa {attempt + 1} para {user_id}, retry em {wait}s: {error}")
                await asyncio.sleep(wait)
            else:
                logger.error(f"Erro definitivo para {user_id}: {error}")
                log_event(logger, "daily_verse_send_failed", level=logging.ERROR, telegram_user_id=user_id, error=str(error))
                return False

    return False


if __name__ == "__main__":
    asyncio.run(main())
