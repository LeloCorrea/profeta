"""Controle de orçamento de mensagens automáticas por usuário/dia.

Aplica-se apenas a: campanhas, lembretes e upsell.
NÃO aplica ao versículo diário (benefício core da assinatura).

Atomicidade: cada check_and_increment abre uma transação explícita (session.begin()).
SQLite WAL serializa escritores — sem race condition em produção (job tem file lock,
bot é single-threaded event loop).
"""
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import DailyMessageBudget

_MAX_DAILY = 4


async def check_and_increment(user_id: str, max_per_day: int = _MAX_DAILY) -> bool:
    """Retorna True e incrementa contador se ainda dentro do budget diário.

    Retorna False sem incrementar se o limite já foi atingido.
    Toda a operação ocorre dentro de uma transação: leitura + escrita são atômicas.
    """
    today = date.today()
    async with SessionLocal() as session:
        async with session.begin():
            record = await session.scalar(
                select(DailyMessageBudget)
                .where(DailyMessageBudget.telegram_user_id == str(user_id))
                .where(DailyMessageBudget.budget_date == today)
            )
            if record is None:
                session.add(DailyMessageBudget(
                    telegram_user_id=str(user_id),
                    budget_date=today,
                    count=1,
                ))
                return True
            if record.count >= max_per_day:
                return False
            record.count += 1
            return True


async def get_count(user_id: str) -> int:
    """Retorna quantas mensagens automáticas já foram enviadas hoje para o usuário."""
    today = date.today()
    async with SessionLocal() as session:
        record = await session.scalar(
            select(DailyMessageBudget)
            .where(DailyMessageBudget.telegram_user_id == str(user_id))
            .where(DailyMessageBudget.budget_date == today)
        )
        return record.count if record else 0
