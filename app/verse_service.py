import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Verse, VerseHistory
from app.observability import get_logger, log_event


logger = get_logger(__name__)
BIBLE_PATH = Path("data/bible/bible.json")
RECENT_VERSE_BLOCK_SIZE = 30


@lru_cache(maxsize=1)
def load_verses() -> list[dict[str, Any]]:
    if not BIBLE_PATH.exists():
        return []

    with open(BIBLE_PATH, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)

    return data if isinstance(data, list) else []


def normalize_verse(verse: Any) -> dict[str, Any]:
    if isinstance(verse, dict):
        return {
            "id": verse.get("id"),
            "book": str(verse.get("book", "")).strip(),
            "chapter": str(verse.get("chapter", "")).strip(),
            "verse": str(verse.get("verse", "")).strip(),
            "text": str(verse.get("text", "")).strip(),
        }

    return {
        "id": getattr(verse, "id", None),
        "book": str(getattr(verse, "book", "")).strip(),
        "chapter": str(getattr(verse, "chapter", "")).strip(),
        "verse": str(getattr(verse, "verse", "")).strip(),
        "text": str(getattr(verse, "text", "")).strip(),
    }


def verse_ref_tuple(verse: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(verse.get("book", "")).strip(),
        str(verse.get("chapter", "")).strip(),
        str(verse.get("verse", "")).strip(),
    )


def history_ref_tuple(item: Any) -> tuple[str, str, str]:
    return (
        str(getattr(item, "book", "")).strip(),
        str(getattr(item, "chapter", "")).strip(),
        str(getattr(item, "verse", "")).strip(),
    )


def format_verse_reference(verse: dict[str, Any]) -> str:
    return f"{verse['book']} {verse['chapter']}:{verse['verse']}"


def format_verse_text(verse: dict[str, Any], journey_title: Optional[str] = None) -> str:
    journey_line = f"Trilha ativa: {journey_title}\n\n" if journey_title else ""
    return (
        f"📖 {format_verse_reference(verse)}\n\n"
        f"{journey_line}"
        f"“{verse['text']}”"
    )


def build_tts_text(verse: dict[str, Any]) -> str:
    return (
        "Versículo do dia. "
        f"{verse['book']}, capítulo {verse['chapter']}, versículo {verse['verse']}. "
        f"{verse['text']}"
    )


async def get_recent_verse_refs_for_user(
    telegram_user_id: str,
    limit: int = RECENT_VERSE_BLOCK_SIZE,
) -> set[tuple[str, str, str]]:
    async with SessionLocal() as session:
        stmt = (
            select(VerseHistory)
            .where(VerseHistory.telegram_user_id == str(telegram_user_id))
            .order_by(VerseHistory.id.desc())
            .limit(limit)
        )
        items = (await session.execute(stmt)).scalars().all()

    return {history_ref_tuple(item) for item in items}


async def get_random_verse_from_db(
    excluded_refs: Optional[set[tuple[str, str, str]]] = None,
) -> Optional[dict[str, Any]]:
    excluded_refs = excluded_refs or set()
    fetch_limit = max(1, len(excluded_refs) + 5)

    async with SessionLocal() as session:
        stmt = select(Verse).order_by(func.random()).limit(fetch_limit)
        candidates = [normalize_verse(v) for v in (await session.execute(stmt)).scalars().all()]

    if not candidates:
        return None

    available = [v for v in candidates if verse_ref_tuple(v) not in excluded_refs]
    verse = available[0] if available else candidates[0]
    strategy = "random_order" if available else "random_order_fallback"
    log_event(logger, "verse_selected_from_db", verse_reference=format_verse_reference(verse), strategy=strategy)
    return verse


def get_random_verse_from_json(
    excluded_refs: Optional[set[tuple[str, str, str]]] = None,
) -> Optional[dict[str, Any]]:
    excluded_refs = excluded_refs or set()
    verses = [normalize_verse(item) for item in load_verses()]
    if not verses:
        return None

    filtered = [verse for verse in verses if verse_ref_tuple(verse) not in excluded_refs]
    verse = random.choice(filtered or verses)
    log_event(logger, "verse_selected_from_json", verse_reference=format_verse_reference(verse))
    return verse


async def get_random_verse_for_user(telegram_user_id: str) -> Optional[dict[str, Any]]:
    recent_refs = await get_recent_verse_refs_for_user(telegram_user_id)
    verse = await get_random_verse_from_db(recent_refs)
    if verse:
        return verse
    return get_random_verse_from_json(recent_refs)


async def save_verse_history(telegram_user_id: str, verse: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        session.add(
            VerseHistory(
                telegram_user_id=str(telegram_user_id),
                book=str(verse["book"]),
                chapter=str(verse["chapter"]),
                verse=str(verse["verse"]),
                text=str(verse["text"]),
            )
        )
        await session.commit()

    log_event(
        logger,
        "verse_history_saved",
        telegram_user_id=telegram_user_id,
        verse_reference=format_verse_reference(verse),
    )


async def search_verses_by_keyword(keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = (
            select(Verse)
            .where(Verse.text.ilike(f"%{keyword}%"))
            .order_by(func.random())
            .limit(limit)
        )
        results = (await session.execute(stmt)).scalars().all()

    if results:
        return [normalize_verse(v) for v in results]

    kw = keyword.lower()
    matches = [normalize_verse(v) for v in load_verses() if kw in v.get("text", "").lower()]
    if matches:
        return random.sample(matches, min(limit, len(matches)))
    return []


async def get_last_verse_for_user(telegram_user_id: str) -> Optional[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = (
            select(VerseHistory)
            .where(VerseHistory.telegram_user_id == str(telegram_user_id))
            .order_by(VerseHistory.id.desc())
            .limit(1)
        )
        item = (await session.execute(stmt)).scalar_one_or_none()

    return normalize_verse(item) if item else None
