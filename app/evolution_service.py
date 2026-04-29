"""
User evolution/gamification service.

Reads verse_history and classified verses to compute progress metrics.
This service is read-only — it never modifies any data.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import cast, func, select
from sqlalchemy import String as SAString

from app.db import SessionLocal
from app.models import User, Verse, VerseHistory
from app.trilha_service import TRILHA_NAMES
from app.verse_classifier import INTERNAL_DEFAULT_TRILHA

TOTAL_BIBLE_VERSES = 31102

_SP_TZ = ZoneInfo("America/Sao_Paulo")

LEVELS: list[tuple[int, str]] = [
    (0, "🌱 Iniciante"),
    (10, "🔥 Caminhando"),
    (50, "💪 Constante"),
    (150, "🕊️ Profundo"),
    (400, "👑 Mestre"),
]

MOTIVATIONAL_MESSAGES: list[tuple[int, str]] = [
    (0, "✨ Cada versículo que você lê transforma sua vida."),
    (10, "✨ Você está criando um hábito que vai durar."),
    (50, "🔥 Sua disciplina está crescendo dia após dia."),
    (150, "🕊️ Você está avançando de forma consistente."),
    (400, "👑 Sua jornada com a Palavra é inspiradora."),
]


def get_user_level(total_read: int) -> dict[str, Any]:
    """Return level info for a given total_read count."""
    current_idx = 0
    for i, (threshold, _) in enumerate(LEVELS):
        if total_read >= threshold:
            current_idx = i

    current_name = LEVELS[current_idx][1]
    if current_idx + 1 < len(LEVELS):
        next_threshold, next_name = LEVELS[current_idx + 1]
        remaining = next_threshold - total_read
    else:
        next_name = None
        remaining = None

    return {
        "name": current_name,
        "next_name": next_name,
        "remaining": remaining,
    }


def _get_motivational_message(total_read: int) -> str:
    msg = MOTIVATIONAL_MESSAGES[0][1]
    for threshold, text in MOTIVATIONAL_MESSAGES:
        if total_read >= threshold:
            msg = text
    return msg


async def get_user_streak(telegram_user_id: str) -> int:
    """
    Return the current daily streak for the user (SP timezone).
    Streak = consecutive days (ending today or yesterday) with at least one verse received.
    A one-day gap resets the streak to 0.
    """
    async with SessionLocal() as session:
        timestamps = (
            await session.execute(
                select(VerseHistory.created_at)
                .where(VerseHistory.telegram_user_id == telegram_user_id)
            )
        ).scalars().all()

    if not timestamps:
        return 0

    sp_dates: set[date] = set()
    for dt in timestamps:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        sp_dates.add(dt.astimezone(_SP_TZ).date())

    today = datetime.now(_SP_TZ).date()

    sorted_dates = sorted(sp_dates, reverse=True)
    if sorted_dates[0] < today - timedelta(days=1):
        return 0

    streak = 1
    for i in range(1, len(sorted_dates)):
        if sorted_dates[i] == sorted_dates[i - 1] - timedelta(days=1):
            streak += 1
        else:
            break
    return streak


async def get_user_evolution(telegram_user_id: str) -> dict[str, Any]:
    """
    Return progress metrics for a user.

    Result shape:
        {
            "total_read": int,
            "percent_bible": float,
            "selected_trilha": str | None,          # user's current trilha key
            "selected_trilha_label": str | None,    # display label
            "streak": int,
            "level": {"name": str, "next_name": str|None, "remaining": int|None},
            "motivational": str,
            "trilhas": {
                "<key>": {
                    "count": int,
                    "label": str,
                    "percent": float,
                },
                ...
            }
        }
    """
    async with SessionLocal() as session:
        total_read: int = (
            await session.execute(
                select(func.count())
                .select_from(VerseHistory)
                .where(VerseHistory.telegram_user_id == telegram_user_id)
            )
        ).scalar_one()

        user_row = (
            await session.execute(
                select(User.selected_trilha)
                .where(User.telegram_user_id == telegram_user_id)
            )
        ).first()

        trilha_total_rows = (
            await session.execute(
                select(Verse.trilha, func.count().label("cnt"))
                .where(Verse.trilha.isnot(None))
                .where(Verse.trilha != INTERNAL_DEFAULT_TRILHA)
                .group_by(Verse.trilha)
            )
        ).all()
        trilha_totals: dict[str, int] = {row[0]: row[1] for row in trilha_total_rows}

        user_trilha_rows = (
            await session.execute(
                select(Verse.trilha, func.count().label("cnt"))
                .join(
                    VerseHistory,
                    (VerseHistory.book == Verse.book)
                    & (VerseHistory.chapter == cast(Verse.chapter, SAString))
                    & (VerseHistory.verse == cast(Verse.verse, SAString))
                    & (VerseHistory.telegram_user_id == telegram_user_id),
                )
                .where(Verse.trilha.isnot(None))
                .where(Verse.trilha != INTERNAL_DEFAULT_TRILHA)
                .group_by(Verse.trilha)
            )
        ).all()

    selected_trilha: Optional[str] = user_row[0] if user_row else None
    selected_trilha_label: Optional[str] = TRILHA_NAMES.get(selected_trilha) if selected_trilha else None

    percent_bible = round(total_read / TOTAL_BIBLE_VERSES * 100, 2) if total_read else 0.0

    trilhas: dict[str, dict[str, Any]] = {}
    for row in user_trilha_rows:
        trilha_key, count = row[0], int(row[1])
        if trilha_key not in TRILHA_NAMES:
            continue
        total_in_trilha = trilha_totals.get(trilha_key, 0)
        percent = round(count / total_in_trilha * 100, 1) if total_in_trilha > 0 else 0.0
        trilhas[trilha_key] = {
            "count": count,
            "label": TRILHA_NAMES[trilha_key],
            "percent": percent,
        }

    streak = await get_user_streak(telegram_user_id)

    return {
        "total_read": total_read,
        "percent_bible": percent_bible,
        "selected_trilha": selected_trilha,
        "selected_trilha_label": selected_trilha_label,
        "streak": streak,
        "level": get_user_level(total_read),
        "motivational": _get_motivational_message(total_read),
        "trilhas": trilhas,
    }
