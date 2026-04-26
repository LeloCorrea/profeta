import asyncio
from app.db import engine
from app.models import Base


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✅ Tabelas criadas com sucesso!")


if __name__ == "__main__":
    asyncio.run(main())
