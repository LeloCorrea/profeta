"""
Alert service — Telegram notifications when system health degrades.

Anti-spam: 1 alert per level per calendar day (São Paulo timezone).
State persisted in data/alert_state.json — no DB required.
Never raises — all errors are logged and swallowed.

Usage (end of job / retry):
    await check_and_send_alert(missing_today=N, delivered_today=M, active_count=K, is_after_retry=True)
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

from app.config import (
    ALERT_CHAT_ID,
    ENABLE_ALERTS,
    PROJECT_ROOT,
    PUBLIC_BASE_URL,
    TELEGRAM_BOT_TOKEN,
    WARNING_PERSIST_MINUTES,
    WARNING_THRESHOLD,
)
from app.observability import get_logger, log_event

logger = get_logger(__name__)

try:
    from zoneinfo import ZoneInfo
    _TZ_SP = ZoneInfo("America/Sao_Paulo")
except ImportError:
    _TZ_SP = timezone(timedelta(hours=-3))  # type: ignore[assignment]

_STATE_FILE = PROJECT_ROOT / "data" / "alert_state.json"

_EMPTY_STATE: dict = {
    "date": "",
    "warning_first_seen_at": None,
    "sent_warning_at": None,
    "sent_critical_at": None,
}


# ── State persistence ─────────────────────────────────────────────────────────

def _now_sp() -> datetime:
    return datetime.now(_TZ_SP)


def _today_sp() -> date:
    return _now_sp().date()


def _load_state() -> dict:
    today = str(_today_sp())
    try:
        if _STATE_FILE.exists():
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            if state.get("date") != today:
                return {**_EMPTY_STATE, "date": today}
            return state
    except Exception:
        pass
    return {**_EMPTY_STATE, "date": today}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as err:
        log_event(logger, "alert_state_save_failed", error=str(err))


# ── Telegram delivery ─────────────────────────────────────────────────────────

async def _send_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not ALERT_CHAT_ID:
        return False
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=ALERT_CHAT_ID, text=text)
        log_event(logger, "alert_sent", chat_id=ALERT_CHAT_ID, preview=text[:80])
        return True
    except TelegramError as err:
        log_event(logger, "alert_telegram_failed", error=str(err), level=40)
        return False


# ── Formatting helpers ────────────────────────────────────────────────────────

def _delivery_rate(delivered: Optional[int], active: Optional[int]) -> str:
    if delivered is None or active is None or active == 0:
        return "—"
    return f"{delivered / active * 100:.1f}%"


def _dashboard_link() -> str:
    if PUBLIC_BASE_URL:
        return f"\n🔗 Dashboard: {PUBLIC_BASE_URL.rstrip('/')}/admin"
    return ""


# ── Public entry point ────────────────────────────────────────────────────────

async def check_and_send_alert(
    *,
    missing_today: int,
    delivered_today: Optional[int] = None,
    active_count: Optional[int] = None,
    is_after_retry: bool = False,
) -> None:
    """
    Evaluate system health and send a Telegram alert to ALERT_CHAT_ID if warranted.

    CRITICAL  — immediate, once per day, fires when missing > 0 after retry.
    WARNING   — fires when missing > WARNING_THRESHOLD OR persisting > WARNING_PERSIST_MINUTES.
    Anti-spam — escalation (WARNING → CRITICAL) still sends; new day resets counters.
    """
    if not ENABLE_ALERTS:
        return

    if not ALERT_CHAT_ID:
        log_event(logger, "alert_skipped", reason="ALERT_CHAT_ID_not_configured")
        return

    try:
        await _evaluate(
            missing_today=missing_today,
            delivered_today=delivered_today,
            active_count=active_count,
            is_after_retry=is_after_retry,
        )
    except Exception as err:
        log_event(logger, "alert_check_failed", error=str(err), level=40)


# ── Internal evaluation ───────────────────────────────────────────────────────

async def _evaluate(
    *,
    missing_today: int,
    delivered_today: Optional[int],
    active_count: Optional[int],
    is_after_retry: bool,
) -> None:
    state = _load_state()
    now_iso = _now_sp().isoformat(timespec="seconds")
    rate = _delivery_rate(delivered_today, active_count)
    link = _dashboard_link()

    # ── CRITICAL: still missing after retry ──────────────────────────────────
    if is_after_retry and missing_today > 0:
        if state.get("sent_critical_at"):
            log_event(logger, "alert_skipped", reason="critical_already_sent_today")
            return

        text = (
            f"🚨 CRITICAL — entrega falhou após retry\n"
            f"missing: {missing_today}\n"
            f"delivery_rate: {rate}"
            f"{link}\n\n"
            f"→ Abrir dashboard\n"
            f"→ Executar retry manual"
        )
        if await _send_telegram(text):
            state["sent_critical_at"] = now_iso
            _save_state(state)
        return

    # ── OK: clear warning tracker ─────────────────────────────────────────────
    if missing_today == 0:
        if state.get("warning_first_seen_at"):
            state["warning_first_seen_at"] = None
            _save_state(state)
        return

    # ── WARNING ───────────────────────────────────────────────────────────────
    if state.get("sent_warning_at"):
        log_event(logger, "alert_skipped", reason="warning_already_sent_today")
        return

    # Record first-seen timestamp (idempotent)
    if not state.get("warning_first_seen_at"):
        state["warning_first_seen_at"] = now_iso
        _save_state(state)
        log_event(logger, "alert_warning_first_seen", missing=missing_today)

    # Decide whether to send now
    should_send = False
    reason = ""

    if missing_today > WARNING_THRESHOLD:
        should_send = True
        reason = f"missing={missing_today} > threshold={WARNING_THRESHOLD}"
    else:
        first_seen_str = state.get("warning_first_seen_at")
        if first_seen_str:
            try:
                first_seen = datetime.fromisoformat(first_seen_str)
                elapsed = (_now_sp() - first_seen).total_seconds() / 60
                if elapsed >= WARNING_PERSIST_MINUTES:
                    should_send = True
                    reason = f"WARNING persistente há {int(elapsed)}min"
            except Exception:
                pass

    if not should_send:
        log_event(
            logger, "alert_warning_deferred",
            missing=missing_today, first_seen=state.get("warning_first_seen_at"),
        )
        return

    text = (
        f"⚠ WARNING persistente\n"
        f"missing: {missing_today}\n"
        f"delivery_rate: {rate}"
        f"{link}"
    )
    if await _send_telegram(text):
        state["sent_warning_at"] = now_iso
        _save_state(state)
        log_event(logger, "alert_warning_sent", missing=missing_today, reason=reason)
