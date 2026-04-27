import asyncio
import logging

from sqlalchemy import text

import app.models  # noqa: F401 — registra todos os modelos no metadata
from app.db import Base, engine

logger = logging.getLogger(__name__)


async def init() -> None:
    """Cria todas as tabelas definidas nos models se ainda não existirem.
    Operação idempotente: não apaga dados existentes."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_tables_initialized")


async def validate_database_schema() -> set[str]:
    """Verifica se todas as tabelas dos models existem no banco.
    Loga claramente as ausências para facilitar diagnóstico em produção."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        existing: set[str] = {row[0] for row in result.fetchall()}

    required: set[str] = {table.name for table in Base.metadata.sorted_tables}
    missing = required - existing

    if missing:
        logger.warning(
            "missing_tables_detected | tables: %s",
            ", ".join(sorted(missing)),
        )
        from app.config import ENV
        if ENV.lower() in {"prod", "production"}:
            raise RuntimeError(
                f"Tabelas obrigatórias ausentes em produção: {', '.join(sorted(missing))}. "
                "Execute `python -m app.init_db` antes de subir o serviço."
            )
    else:
        logger.info("database_schema_validated")

    return missing


if __name__ == "__main__":
    asyncio.run(init())
