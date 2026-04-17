"""
Script utilitário para identificar e invalidar explicações mockadas/fallback no banco.
Pode marcar como inválidas ou remover, conforme critério definido.
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, update, delete
from app.models import VerseExplanation
import os

DB_URL = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///../db.sqlite3"

# Critério conservador: explicações muito curtas, iguais entre si, ou com palavras-chave de fallback
FALLBACK_KEYWORDS = [
    "Essência:", "Contexto:", "Aplicação:", "Oração:", "Esta é uma explicação genérica", "Reflexão genérica"
]

async def main(action="mark_invalid"):
    engine = create_async_engine(DB_URL)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        stmt = select(VerseExplanation)
        rows = (await session.execute(stmt)).scalars().all()
        to_invalidate = []
        for row in rows:
            text = (row.explanation or "").lower()
            if any(k.lower() in text for k in FALLBACK_KEYWORDS) or len(text) < 80:
                to_invalidate.append(row)
        print(f"Encontradas {len(to_invalidate)} explicações mockadas/fallback.")
        if action == "mark_invalid":
            for row in to_invalidate:
                row.explanation = "[INVALIDADA - Fallback antigo removido]"
            await session.commit()
            print("Marcadas como inválidas.")
        elif action == "delete":
            for row in to_invalidate:
                await session.delete(row)
            await session.commit()
            print("Registros removidos.")
        else:
            print("Ação não reconhecida.")

if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "mark_invalid"
    asyncio.run(main(action))
