import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Optional

from sqlalchemy import func, select
from telegram import Bot
from telegram.error import TelegramError

try:
    from zoneinfo import ZoneInfo
    _TZ_SP = ZoneInfo("America/Sao_Paulo")
except Exception:
    _TZ_SP = timezone(timedelta(hours=-3))  # type: ignore[assignment]

from app.audio_service import cleanup_old_audio_files, ensure_named_audio_asset
from app.bot_flows import compute_content_id
from app.config import APP_NAME, AUDIO_MAX_AGE_DAYS, ENV, TELEGRAM_BOT_TOKEN, missing_settings
from app.db import SessionLocal
from app.models import Subscription, User
from app.observability import log_event
from app.premium_experience import build_verse_actions_keyboard
from app.subscription_service import (
    expire_overdue_subscriptions,
    get_users_expiring_in_window,
)
from app.verse_service import build_tts_text, format_verse_text, get_random_verse_for_user, save_verse_history
from app.alert_service import check_and_send_alert

_FATAL_TELEGRAM_KEYWORDS = ("blocked", "bot was blocked", "deactivated", "kicked", "chat not found", "user not found")

LOGS_DIR = Path("logs")
LOG_FILE = LOGS_DIR / "daily_job.log"
LOCK_FILE = LOGS_DIR / "daily_job.lock"
DATE_MARKER_FILE = LOGS_DIR / "daily_job_date.txt"

_BATCH_SIZE = 500


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _today_sp() -> date:
    return datetime.now(_TZ_SP).date()


def _sp_day_to_utc_range(d: date) -> tuple[datetime, datetime]:
    """Returns (start_utc, end_utc) as naive UTC datetimes covering the full
    calendar day `d` in São Paulo timezone."""
    start_sp = datetime(d.year, d.month, d.day, tzinfo=_TZ_SP)
    end_sp = start_sp + timedelta(days=1)
    start_utc = start_sp.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_sp.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


async def get_users_missing_delivery(target_date: Optional[date] = None) -> list[str]:
    """Returns telegram_user_ids of active subscribers who have no VerseHistory
    record on target_date (São Paulo calendar day). Defaults to today."""
    if target_date is None:
        target_date = _today_sp()

    start_utc, end_utc = _sp_day_to_utc_range(target_date)

    async with SessionLocal() as session:
        now_utc = datetime.utcnow()

        active_stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until > now_utc)
        )
        active_result = await session.execute(active_stmt)
        active_ids: set[str] = {row[0] for row in active_result.all() if row[0]}

        if not active_ids:
            return []

        from app.models import VerseHistory
        delivered_stmt = (
            select(VerseHistory.telegram_user_id)
            .where(VerseHistory.created_at >= start_utc)
            .where(VerseHistory.created_at < end_utc)
            .distinct()
        )
        delivered_result = await session.execute(delivered_stmt)
        delivered_ids: set[str] = {row[0] for row in delivered_result.all() if row[0]}

        return sorted(active_ids - delivered_ids)


def _read_lock_pid() -> Optional[int]:
    """Returns the PID written in the lock file, or None if unreadable."""
    try:
        content = LOCK_FILE.read_text().strip()
        return int(content) if content.isdigit() else None
    except Exception:
        return None


def _is_pid_alive(pid: int) -> bool:
    """Returns True if a process with the given PID exists on this system."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but we lack permission to signal it
    except OSError:
        return False


def _acquire_job_lock() -> Optional[IO]:
    """
    Acquires an exclusive file lock to prevent concurrent job execution.
    Returns an open file handle (lock held) on success, None if another instance
    is already running.

    Uses fcntl.flock on Linux/macOS: atomic, no race condition, and automatically
    released by the kernel if the process dies unexpectedly (crash-safe).
    If the lock file contains a dead PID (edge case), clears it and retries once.
    On non-Unix systems (Windows dev/CI), returns a no-op handle so tests run normally.
    """
    try:
        import fcntl
    except ImportError:
        # Windows dev/CI — lock not enforced; tests pass transparently
        return open(os.devnull, "w")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    def _try_flock() -> Optional[IO]:
        fh = open(LOCK_FILE, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.write(str(os.getpid()))
            fh.flush()
            return fh
        except BlockingIOError:
            fh.close()
            return None

    lock_fh = _try_flock()
    if lock_fh is not None:
        return lock_fh

    # flock failed — check if the owning process is still alive
    locked_pid = _read_lock_pid()
    if locked_pid is not None and not _is_pid_alive(locked_pid):
        # Stale lock: process died without releasing (edge case on some filesystems).
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        lock_fh = _try_flock()
        if lock_fh is not None:
            return lock_fh

    return None


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
    now = datetime.utcnow()
    async with SessionLocal() as session:
        stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until > now)
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]


async def _log_daily_metrics(
    logger: logging.Logger,
    active_count: int,
    verses_sent: int,
    message_failures: int = 0,
    audio_failures: int = 0,
) -> None:
    """Coleta e loga métricas estruturadas ao final do job diário."""
    try:
        from app.models import UserMission, UserSegment
        today = date.today()
        async with SessionLocal() as session:
            missions_created = await session.scalar(
                select(func.count()).select_from(UserMission).where(UserMission.assigned_date == today)
            )
            missions_completed = await session.scalar(
                select(func.count()).select_from(UserMission)
                .where(UserMission.assigned_date == today)
                .where(UserMission.status == "completed")
            )
            seg_result = await session.execute(
                select(UserSegment.segment, func.count(UserSegment.id)).group_by(UserSegment.segment)
            )
            segment_dist: dict[str, int] = {row[0]: row[1] for row in seg_result.all()}

        total = active_count or 1
        message_failure_rate = round(message_failures / total, 4)
        audio_failure_rate = round(audio_failures / max(verses_sent, 1), 4)

        log_event(
            logger, "daily_metrics",
            active_users_count=active_count,
            verses_sent_count=verses_sent,
            missions_created_count=missions_created or 0,
            missions_completed_count=missions_completed or 0,
            segment_warm=segment_dist.get("WARM", 0),
            segment_at_risk=segment_dist.get("AT_RISK", 0),
            segment_cold=segment_dist.get("COLD", 0),
            message_failures=message_failures,
            message_failure_rate=message_failure_rate,
            audio_failures=audio_failures,
            audio_failure_rate=audio_failure_rate,
        )
    except Exception as metrics_err:
        logger.warning(f"[Métricas] Falha ao coletar métricas diárias: {metrics_err}")


async def _log_retention_metrics(logger: logging.Logger) -> None:
    """Loga snapshot de retenção: usuários ativos nas últimas 1, 3 e 7 dias."""
    try:
        from app.models import UserStats
        now = datetime.utcnow()
        async with SessionLocal() as session:
            d1 = await session.scalar(
                select(func.count(UserStats.id)).where(UserStats.last_activity_at >= now - timedelta(days=1))
            )
            d3 = await session.scalar(
                select(func.count(UserStats.id)).where(UserStats.last_activity_at >= now - timedelta(days=3))
            )
            d7 = await session.scalar(
                select(func.count(UserStats.id)).where(UserStats.last_activity_at >= now - timedelta(days=7))
            )
        log_event(logger, "retention_metrics", retention_1d=d1 or 0, retention_3d=d3 or 0, retention_7d=d7 or 0)
    except Exception as e:
        logger.warning(f"[Retenção] Falha ao calcular métricas de retenção: {e}")


async def main() -> None:
    logger = setup_logger()

    # ── Lock de concorrência ──────────────────────────────────────────────────
    lock = _acquire_job_lock()
    if lock is None:
        locked_pid = _read_lock_pid()
        logger.warning(
            "===== JOB DIARIO JA EM EXECUCAO (lock ativo) — ABORTANDO ===== pid_bloqueante=%s",
            locked_pid,
        )
        log_event(logger, "daily_job_already_running", level=logging.WARNING, locked_pid=locked_pid)
        return

    try:
        missing = missing_settings("TELEGRAM_BOT_TOKEN")
        if missing:
            log_event(
                logger,
                "daily_job_configuration_error",
                level=logging.ERROR,
                missing_settings=", ".join(missing),
            )
            raise RuntimeError(f"Configuracao obrigatoria ausente para job diario: {', '.join(missing)}")

        job_date = _today_sp().isoformat()
        try:
            if DATE_MARKER_FILE.exists() and DATE_MARKER_FILE.read_text().strip() == job_date:
                logger.warning("===== JOB JA EXECUTADO HOJE (%s) — ABORTANDO =====", job_date)
                log_event(logger, "daily_job_already_ran_today", job_date=job_date, level=logging.WARNING)
                return
        except Exception as marker_err:
            logger.warning("Falha ao ler marcador de data: %s", marker_err)

        logger.info("===== INICIO DO JOB DIARIO | job_date=%s =====", job_date)
        log_event(logger, "daily_job_started", app_name=APP_NAME, env=ENV, job_date=job_date)

        bot = Bot(token=TELEGRAM_BOT_TOKEN)

        # ── 1. Expirar assinaturas vencidas e notificar usuários ──────────────────
        expired_count, expired_user_ids = await expire_overdue_subscriptions()
        if expired_count:
            logger.info(f"Assinaturas expiradas desativadas: {expired_count}")
            for uid in expired_user_ids:
                await _send_expiry_notification(uid, bot, logger)

        # ── 2. Lembretes de renovação: 7 dias, 3 dias e 1 dia antes ──────────────
        reminder_windows = [
            (6, 7, 7),   # expira entre 6 e 7 dias → avisa "7 dias"
            (2, 3, 3),   # expira entre 2 e 3 dias → avisa "3 dias"
            (0, 1, 1),   # expira entre 0 e 1 dia  → avisa "hoje"
        ]
        for days_min, days_max, days_display in reminder_windows:
            expiring = await get_users_expiring_in_window(days_min, days_max)
            for uid in expiring:
                await _send_renewal_reminder(uid, days_display, bot, logger)
            if expiring:
                logger.info(f"Lembretes de {days_display}d enviados: {len(expiring)}")

        # ── 3. Limpeza de áudio antigo ────────────────────────────────────────────
        cleaned = cleanup_old_audio_files(max_age_days=AUDIO_MAX_AGE_DAYS)
        if cleaned:
            logger.info(f"Arquivos de áudio antigos removidos: {cleaned}")

        # ── 4. Envio diário do versículo ──────────────────────────────────────────
        user_ids = await get_active_user_ids()
        logger.info(f"Usuarios ativos encontrados: {len(user_ids)}")
        log_event(logger, "daily_job_active_users_loaded", active_user_count=len(user_ids))

        if not user_ids:
            return

        sent = 0
        failed_ids: list[str] = []
        audio_failures = 0

        for batch_idx, batch in enumerate(_batched(user_ids, _BATCH_SIZE), 1):
            logger.info(f"Processando batch {batch_idx} | size={len(batch)} | enviados_até_aqui={sent}")
            for user_id in batch:
                try:
                    success, audio_fail = await _send_verse_with_retry(user_id, bot, logger)
                    if audio_fail:
                        audio_failures += 1
                except Exception as loop_err:
                    logger.error(f"[Loop] Erro inesperado para {user_id}: {loop_err}")
                    success = False
                if success:
                    sent += 1
                else:
                    failed_ids.append(user_id)
            await asyncio.sleep(0)

        # ── Second pass: retry único para usuários que falharam ──────────────────
        retry_sent = 0
        if failed_ids:
            logger.info(f"Iniciando segunda tentativa | falhas={len(failed_ids)}")
            still_failed: list[str] = []
            for user_id in failed_ids:
                try:
                    success, audio_fail = await _send_verse_with_retry(user_id, bot, logger, max_attempts=1)
                    if audio_fail:
                        audio_failures += 1
                except Exception:
                    success = False
                if success:
                    retry_sent += 1
                else:
                    still_failed.append(user_id)
            if retry_sent:
                logger.info(f"Segunda tentativa recuperou: {retry_sent}/{len(failed_ids)}")
                log_event(logger, "daily_job_retry_recovery", recovered=retry_sent)
            failed_ids = still_failed

        failed = len(failed_ids)
        logger.info(
            "===== FIM DO JOB DIARIO | total=%s | enviados=%s | retry_recuperados=%s | falhas_finais=%s =====",
            len(user_ids), sent, retry_sent, failed,
        )
        log_event(
            logger, "daily_job_finished",
            total=len(user_ids), sent=sent, retry_recovered=retry_sent, failed=failed,
        )
        if failed_ids:
            logger.warning(f"Usuários sem entrega após todas as tentativas: {failed_ids}")

        # ── Verificação de garantia de entrega ───────────────────────────────────
        missing_after: list[str] = []
        try:
            missing_after = await get_users_missing_delivery()
            logger.info(f"missing_today={len(missing_after)}")
            if missing_after:
                logger.warning(f"Usuários ainda sem versículo hoje: {missing_after}")
                log_event(logger, "daily_job_missing_after_run", missing=len(missing_after))
        except Exception as chk_err:
            logger.warning(f"[Delivery Check] Falha na verificação pós-job: {chk_err}")

        # ── 4b. Criar missão diária para todos os assinantes ativos ──────────────
        try:
            from app.services.mission_service import create_daily_mission
            mission_errors = 0
            for batch in _batched(user_ids, _BATCH_SIZE):
                for uid in batch:
                    try:
                        await create_daily_mission(uid)
                    except Exception as me:
                        mission_errors += 1
                        logger.warning(f"[Missão] Falha ao criar missão para {uid}: {me}")
                await asyncio.sleep(0)
            logger.info(f"Missões diárias criadas | total={len(user_ids)} errors={mission_errors}")
        except Exception as miss_err:
            logger.warning(f"[Missão] Falha no bloco de missões diárias: {miss_err}")

        # ── 5. Reengajamento: lembretes de missão pendente ────────────────────────
        await _send_mission_reminders(bot, logger)

        # ── 6. Reengajamento: usuários inativos há mais de 2 dias ─────────────────
        await _send_inactivity_reminders(bot, logger)

        # ── 6b. Atualizar segmentos de todos os assinantes ativos ─────────────────
        # Deve rodar antes das campanhas para que COLD/AT_RISK reflitam o estado real.
        try:
            from app.services.segment_service import refresh_segments_for_users
            await refresh_segments_for_users(user_ids)
        except Exception as seg_err:
            logger.warning(f"[Segmento] Falha ao atualizar segmentos: {seg_err}")

        # ── 7. Campanhas por segmento (COLD / AT_RISK) ────────────────────────────
        await _send_segment_campaigns(bot, logger)

        # ── 7b. Upsell para usuários WARM de alta atividade ───────────────────────
        await _send_upsell_messages(bot, logger)

        # ── 8. Alertas de saúde ───────────────────────────────────────────────────
        await check_and_send_alert(
            missing_today=len(missing_after),
            delivered_today=sent,
            active_count=len(user_ids),
            is_after_retry=False,
        )

        # ── 9. Métricas estruturadas do dia ───────────────────────────────────────
        await _log_daily_metrics(
            logger,
            active_count=len(user_ids),
            verses_sent=sent + retry_sent,
            message_failures=failed,
            audio_failures=audio_failures,
        )
        await _log_retention_metrics(logger)

        # ── Marcar execução bem-sucedida (impede re-execução no mesmo dia) ────────
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            DATE_MARKER_FILE.write_text(job_date)
        except Exception as marker_err:
            logger.warning("Falha ao gravar marcador de data: %s", marker_err)

    finally:
        lock.close()


async def _send_verse_with_retry(user_id: str, bot: Bot, logger: logging.Logger, max_attempts: int = 3) -> tuple[bool, bool]:
    """Returns (message_success, audio_failed)."""
    audio_failed = False
    for attempt in range(max_attempts):
        try:
            verse = await get_random_verse_for_user(user_id)
            if not verse:
                logger.error(f"Nenhum versículo disponível para {user_id}")
                return False, False

            verse_ref = f"{verse['book']} {verse['chapter']}:{verse['verse']}"
            log_event(logger, "daily_verse_send_started", telegram_user_id=user_id, verse_reference=verse_ref)

            # Persist before sending so resolve_last_verse returns this verse
            # immediately when the user taps a button (e.g. Explicar).
            try:
                await save_verse_history(user_id, verse)
            except Exception as hist_err:
                logger.warning(f"[Histórico] Falha ao persistir versículo para {user_id}: {hist_err}")
                log_event(logger, "verse_history_save_failed", telegram_user_id=user_id, error=str(hist_err), level=logging.WARNING)

            try:
                audio_asset = await asyncio.wait_for(
                    ensure_named_audio_asset("versiculo", verse, build_tts_text(verse)),
                    timeout=90.0,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[TTS] Timeout ao gerar áudio para {user_id} — continuando sem áudio")
                audio_asset = None

            content_id = compute_content_id(str(verse.get("text", "")))
            keyboard = build_verse_actions_keyboard(image_content_id=content_id)

            await bot.send_message(
                chat_id=user_id,
                text=format_verse_text(verse),
                reply_markup=keyboard,
            )
            if audio_asset is not None:
                try:
                    with open(audio_asset.path, "rb") as audio:
                        await bot.send_audio(chat_id=user_id, audio=audio, title="Versículo do dia")
                except Exception as audio_err:
                    audio_failed = True
                    logger.warning(f"[Áudio] Falha ao enviar áudio para {user_id}: {audio_err}")
                    log_event(logger, "audio_send_failed", telegram_user_id=user_id, error=str(audio_err), level=logging.WARNING)

            try:
                from app.services.evolution_service import register_activity
                from app.services.evolution_formatter import format_evolution_feedback, get_suggested_next_action
                data = await register_activity(user_id, "verse")
                feedback = format_evolution_feedback(data)
                suggestion = get_suggested_next_action("verse")
                parts = [p for p in [feedback, suggestion] if p]
                if parts:
                    await bot.send_message(chat_id=user_id, text="\n\n".join(parts))
            except Exception as evo_err:
                logger.warning(f"[Evolução] Falha ao processar engajamento para {user_id}: {evo_err}")

            log_event(logger, "daily_verse_send_completed", telegram_user_id=user_id, verse_reference=verse_ref)
            log_event(logger, "user_received_verse", telegram_user_id=user_id, verse_reference=verse_ref, source="verse")

            try:
                from app.services.mission_service import complete_mission
                await complete_mission(user_id)
            except Exception:
                pass

            return True, audio_failed

        except TelegramError as error:
            err_lower = str(error).lower()
            if any(keyword in err_lower for keyword in _FATAL_TELEGRAM_KEYWORDS):
                logger.warning(f"Usuário {user_id} inacessível (fatal): {error}")
                log_event(logger, "daily_verse_send_failed", level=logging.WARNING, telegram_user_id=user_id, error=str(error), fatal=True)
                return False, False
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(f"Tentativa {attempt + 1} falhou para {user_id}, retry em {wait}s: {error}")
                await asyncio.sleep(wait)
            else:
                logger.error(f"Falha definitiva após {max_attempts} tentativas para {user_id}: {error}")
                log_event(logger, "daily_verse_send_failed", level=logging.ERROR, telegram_user_id=user_id, error=str(error), attempts=max_attempts)
                return False, False

        except Exception as error:
            if attempt < max_attempts - 1:
                wait = 2 ** attempt
                logger.warning(f"Erro inesperado na tentativa {attempt + 1} para {user_id}, retry em {wait}s: {error}")
                await asyncio.sleep(wait)
            else:
                logger.error(f"Erro definitivo para {user_id}: {error}")
                log_event(logger, "daily_verse_send_failed", level=logging.ERROR, telegram_user_id=user_id, error=str(error))
                return False, False

    return False, False


async def _send_segment_campaigns(bot: Bot, logger: logging.Logger) -> None:
    try:
        from app.services.segment_service import get_users_by_segment, get_campaign_message
        from app.services.message_budget_service import check_and_increment
        for segment in ("COLD", "AT_RISK"):
            users = await get_users_by_segment(segment)
            if not users:
                continue
            text = get_campaign_message(segment)
            sent = 0
            for uid in users:
                if not await check_and_increment(uid):
                    log_event(logger, "message_budget_exceeded", telegram_user_id=uid, source="campaign", segment=segment)
                    continue
                try:
                    await bot.send_message(chat_id=uid, text=text)
                    sent += 1
                except TelegramError as e:
                    err_lower = str(e).lower()
                    if not any(k in err_lower for k in _FATAL_TELEGRAM_KEYWORDS):
                        logger.warning(f"[Segmento {segment}] Falha ao enviar para {uid}: {e}")
            if sent:
                logger.info(f"Campanha {segment} enviada: {sent}/{len(users)}")
                log_event(logger, "segment_campaign_sent", segment=segment, sent=sent, source="campaign")
    except Exception as e:
        logger.warning(f"[Segmento] Erro no job de campanhas: {e}")


async def _send_inactivity_reminders(bot: Bot, logger: logging.Logger) -> None:
    try:
        from app.services.profile_service import get_inactive_active_subscribers
        from app.services.message_budget_service import check_and_increment
        inactive = await get_inactive_active_subscribers(days=3)
        if not inactive:
            return
        sent = 0
        for uid in inactive:
            log_event(logger, "user_became_inactive", telegram_user_id=uid)
            if not await check_and_increment(uid):
                log_event(logger, "message_budget_exceeded", telegram_user_id=uid, source="inactivity_reminder")
                continue
            try:
                text = (
                    "Você esteve mais distante esses dias...\n\n"
                    "Mas sempre é tempo de recomeçar."
                )
                await bot.send_message(chat_id=uid, text=text)
                sent += 1
            except TelegramError as e:
                err_lower = str(e).lower()
                if not any(k in err_lower for k in _FATAL_TELEGRAM_KEYWORDS):
                    logger.warning(f"[Reengajamento] Falha ao enviar para {uid}: {e}")
        if sent:
            logger.info(f"Lembretes de inatividade enviados: {sent}/{len(inactive)}")
            log_event(logger, "inactivity_reminders_sent", sent=sent, source="inactivity_reminder")
    except Exception as e:
        logger.warning(f"[Reengajamento] Erro no job de inatividade: {e}")


async def _send_mission_reminders(bot: Bot, logger: logging.Logger) -> None:
    try:
        from app.services.mission_service import get_users_with_pending_mission
        from app.services.message_budget_service import check_and_increment
        pending = await get_users_with_pending_mission()
        if not pending:
            return
        sent = 0
        for uid in pending:
            if not await check_and_increment(uid):
                log_event(logger, "message_budget_exceeded", telegram_user_id=uid, source="mission_reminder")
                continue
            try:
                text = (
                    "📋 Você ainda não completou sua missão de hoje.\n\n"
                    "Use /reflexao para continuar sua jornada."
                )
                await bot.send_message(chat_id=uid, text=text)
                sent += 1
            except TelegramError as e:
                err_lower = str(e).lower()
                if not any(k in err_lower for k in _FATAL_TELEGRAM_KEYWORDS):
                    logger.warning(f"[Missão] Falha ao enviar lembrete para {uid}: {e}")
        if sent:
            logger.info(f"Lembretes de missão enviados: {sent}/{len(pending)}")
            log_event(logger, "mission_reminders_sent", sent=sent, source="mission_reminder")
    except Exception as e:
        logger.warning(f"[Missão] Erro no job de reengajamento: {e}")


async def _send_upsell_messages(bot: Bot, logger: logging.Logger) -> None:
    """Envia sugestão leve de premium para usuários WARM com alta atividade (≥7 versículos)."""
    try:
        from app.models import UserStats, UserSegment
        from app.services.message_budget_service import check_and_increment
        async with SessionLocal() as session:
            result = await session.execute(
                select(UserStats.telegram_user_id)
                .join(UserSegment, UserSegment.telegram_user_id == UserStats.telegram_user_id)
                .where(UserSegment.segment == "WARM")
                .where(func.coalesce(UserStats.verse_count, 0) >= 7)
            )
            candidates = [row[0] for row in result.all() if row[0]]

        sent = 0
        for uid in candidates:
            if not await check_and_increment(uid):
                log_event(logger, "message_budget_exceeded", telegram_user_id=uid, source="upsell")
                continue
            try:
                text = (
                    "Você tem sido muito fiel na sua jornada espiritual! 🌟\n\n"
                    "Com o plano premium você acessa conteúdos exclusivos e reflexões mais profundas.\n"
                    "Use /assinar para saber mais."
                )
                await bot.send_message(chat_id=uid, text=text)
                sent += 1
            except TelegramError as e:
                err_lower = str(e).lower()
                if not any(k in err_lower for k in _FATAL_TELEGRAM_KEYWORDS):
                    logger.warning(f"[Upsell] Falha ao enviar para {uid}: {e}")

        if sent:
            logger.info(f"Upsell enviado: {sent}/{len(candidates)}")
            log_event(logger, "upsell_sent", sent=sent, candidates=len(candidates), source="monetization")
    except Exception as e:
        logger.warning(f"[Upsell] Erro no job de upsell: {e}")


async def _send_expiry_notification(user_id: str, bot: Bot, logger: logging.Logger) -> bool:
    try:
        text = (
            "⚠️ Sua assinatura do Profeta expirou.\n\n"
            "Para voltar a receber Palavra, reflexão e áudio diários, use /assinar."
        )
        await bot.send_message(chat_id=user_id, text=text)
        log_event(logger, "expiry_notification_sent", telegram_user_id=user_id)
        return True
    except TelegramError as error:
        err_lower = str(error).lower()
        if any(keyword in err_lower for keyword in _FATAL_TELEGRAM_KEYWORDS):
            logger.warning(f"Usuário {user_id} inacessível (notificação expiração): {error}")
        else:
            logger.error(f"Falha ao notificar expiração para {user_id}: {error}")
        return False


async def _send_renewal_reminder(user_id: str, days_left: int, bot: Bot, logger: logging.Logger) -> bool:
    try:
        if days_left <= 1:
            text = (
                "⚠️ Sua assinatura do Profeta expira hoje.\n\n"
                "Para continuar sua jornada espiritual, use /assinar antes da meia-noite."
            )
        elif days_left <= 3:
            text = (
                f"⏳ Sua assinatura expira em {days_left} dias.\n\n"
                "Quando quiser renovar, use /assinar. Fico aqui com você."
            )
        else:
            text = (
                f"📅 Sua assinatura expira em {days_left} dias.\n\n"
                "Você pode renovar quando quiser com /assinar."
            )
        await bot.send_message(chat_id=user_id, text=text)
        log_event(logger, "renewal_reminder_sent", telegram_user_id=user_id, days_left=days_left)
        return True
    except TelegramError as error:
        err_lower = str(error).lower()
        if any(keyword in err_lower for keyword in _FATAL_TELEGRAM_KEYWORDS):
            logger.warning(f"Usuário {user_id} inacessível (lembrete renovação): {error}")
        else:
            logger.error(f"Falha ao enviar lembrete para {user_id}: {error}")
        return False


async def retry_missing_main() -> None:
    """
    Recovery mode: find active subscribers who haven't received a verse today
    and send it to them. Reuses the same send/persist logic as the main job.

    Invoked via:  python -m app.jobs --retry-missing
    """
    logger = setup_logger()

    missing = missing_settings("TELEGRAM_BOT_TOKEN")
    if missing:
        raise RuntimeError(f"Configuracao ausente para retry-missing: {', '.join(missing)}")

    missing_ids = await get_users_missing_delivery()
    logger.info(f"retry_missing_total={len(missing_ids)}")
    log_event(logger, "retry_missing_started", total=len(missing_ids))

    if not missing_ids:
        logger.info("Nenhum usuário faltante — delivery garantido.")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    sent = 0
    still_failed: list[str] = []

    for user_id in missing_ids:
        try:
            success, _ = await _send_verse_with_retry(user_id, bot, logger)
        except Exception as err:
            logger.error(f"[retry-missing] Erro inesperado para {user_id}: {err}")
            success = False
        if success:
            sent += 1
        else:
            still_failed.append(user_id)

    failed = len(still_failed)
    logger.info(
        "retry_missing_total=%s retry_missing_enviados=%s retry_missing_falhas=%s",
        len(missing_ids), sent, failed,
    )
    log_event(
        logger, "retry_missing_finished",
        total=len(missing_ids), sent=sent, failed=failed,
    )
    if still_failed:
        logger.warning(f"retry-missing: ainda sem entrega após recuperação: {still_failed}")

    await check_and_send_alert(
        missing_today=len(still_failed),
        is_after_retry=True,
    )


if __name__ == "__main__":
    if "--retry-missing" in sys.argv:
        asyncio.run(retry_missing_main())
    else:
        asyncio.run(main())
