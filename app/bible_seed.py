import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Verse
from app.observability import get_logger, log_event

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIBLE_JSON_PATH = _PROJECT_ROOT / "data" / "bible" / "bible.json"

_BATCH_SIZE = 500


async def count_verses(session_factory: async_sessionmaker) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(Verse))
        return result.scalar_one()


async def seed_verses_from_json(
    session_factory: async_sessionmaker,
    bible_path: Optional[Path] = None,
) -> int:
    path = bible_path or BIBLE_JSON_PATH

    if not path.exists():
        log_event(logger, "json_not_found", path=str(path), level=logging.CRITICAL)
        return 0

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.exception("Falha ao carregar JSON de versículos: %s", path)
        return 0

    if not isinstance(data, list) or not data:
        logger.error("bible.json inválido ou vazio: %s", path)
        return 0

    inserted = 0
    batch: list[Verse] = []

    async with session_factory() as session:
        for item in data:
            try:
                reference = f"{item['book']} {item['chapter']}:{item['verse']}"
                batch.append(Verse(
                    book=str(item["book"]),
                    chapter=int(item["chapter"]),
                    verse=int(item["verse"]),
                    text=str(item["text"]),
                    reference=reference,
                ))
                inserted += 1
            except (KeyError, ValueError, TypeError):
                logger.debug("Versículo malformado ignorado: %s", item)
                continue

            if len(batch) >= _BATCH_SIZE:
                session.add_all(batch)
                await session.commit()
                batch.clear()

        if batch:
            session.add_all(batch)
            await session.commit()

    log_event(logger, "verses_seeded_to_db", inserted=inserted, source=str(path))
    return inserted


async def check_and_seed_bible(session_factory: async_sessionmaker) -> None:
    """Called at startup. Logs JSON presence and auto-seeds DB if empty."""
    if not BIBLE_JSON_PATH.exists():
        log_event(logger, "json_not_found", path=str(BIBLE_JSON_PATH), level=logging.CRITICAL)
        logger.critical(
            "BIBLE JSON NÃO ENCONTRADO em %s — versículos dependerão exclusivamente do DB",
            BIBLE_JSON_PATH,
        )
    else:
        log_event(logger, "verses_loaded_from_json", path=str(BIBLE_JSON_PATH))
        logger.info("Bible JSON encontrado: %s", BIBLE_JSON_PATH)

    try:
        count = await count_verses(session_factory)
    except Exception:
        logger.exception("Falha ao verificar contagem de versículos no DB")
        return

    if count > 0:
        logger.info("Tabela verses: %d versículos disponíveis — seed não necessário", count)
        return

    logger.warning("Tabela verses está vazia — iniciando seed automático a partir do JSON")

    if not BIBLE_JSON_PATH.exists():
        logger.error(
            "Seed impossível: JSON não encontrado em %s. "
            "Apenas o fallback hardcoded (Salmos 23:1) estará disponível.",
            BIBLE_JSON_PATH,
        )
        return

    inserted = await seed_verses_from_json(session_factory)
    if inserted > 0:
        log_event(logger, "verses_seeded_to_db", inserted=inserted, source=str(BIBLE_JSON_PATH))
        logger.info("Seed concluído: %d versículos inseridos no DB", inserted)
    else:
        logger.error(
            "Seed falhou — nenhum versículo inserido. "
            "Sistema usará JSON em memória ou fallback hardcoded (Salmos 23:1)."
        )
