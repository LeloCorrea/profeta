import json
import asyncio
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Verse

INPUT_PATH = Path("data/bible/bible.json")


async def main():
    print("📖 Lendo JSON da bíblia...")

    if not INPUT_PATH.exists():
        raise FileNotFoundError("Arquivo bible.json não encontrado")

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    async with SessionLocal() as session:
        total = len(data)
        inserted = 0

        print(f"🔄 Processando {total} versículos...")

        for i, verse in enumerate(data, start=1):
            reference = f"{verse['book']} {verse['chapter']}:{verse['verse']}"

            stmt = select(Verse).where(Verse.reference == reference)
            result = await session.execute(stmt)
            exists = result.scalar_one_or_none()

            if exists:
                continue

            row = Verse(
                book=verse["book"],
                chapter=int(verse["chapter"]),
                verse=int(verse["verse"]),
                text=verse["text"],
                reference=reference,
            )

            session.add(row)
            inserted += 1

            if i % 500 == 0:
                await session.commit()
                print(f"✔ {i}/{total} processados...")

        await session.commit()

    print(f"✅ Import finalizado! Inseridos: {inserted}")


if __name__ == "__main__":
    asyncio.run(main())
