from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Verse


async def get_random_verse_from_db() -> dict | None:
    async with SessionLocal() as session:
        # pega total
        total_stmt = select(func.count()).select_from(Verse)
        total_result = await session.execute(total_stmt)
        total = total_result.scalar()

        if not total:
            return None

        # pega um offset aleatório
        import random
        offset = random.randint(0, total - 1)

        stmt = select(Verse).offset(offset).limit(1)
        result = await session.execute(stmt)
        verse = result.scalar_one_or_none()

        if not verse:
            return None

        return {
            "id": verse.id,
            "book": verse.book,
            "chapter": verse.chapter,
            "verse": verse.verse,
            "text": verse.text,
        }
