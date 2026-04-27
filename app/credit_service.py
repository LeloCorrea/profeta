from datetime import datetime

from sqlalchemy import select, update

from app.db import SessionLocal
from app.db_helpers import get_or_create_user_in_session
from typing import Optional

from app.models import CreditTransaction, Payment, User, UserCredits
from app.observability import get_logger, log_event

logger = get_logger(__name__)


async def get_credits(telegram_id: str) -> int:
    """Returns credits_balance for user, 0 if not found."""
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_id)
        )
        if not user:
            return 0
        row = await session.scalar(
            select(UserCredits).where(UserCredits.user_id == user.id)
        )
        return row.credits_balance if row else 0


async def add_credits(payment_id: str, telegram_id: str, credits: int, value: Optional[float] = None) -> bool:
    """
    Idempotent credit addition. Returns False if payment_id already processed.
    Atomically saves Payment record and upserts UserCredits in one transaction.
    """
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(Payment).where(Payment.provider_payment_id == payment_id)
        )
        if existing:
            log_event(logger, "credit_payment_already_processed", provider_payment_id=payment_id)
            return False

        session.add(Payment(
            provider="asaas",
            provider_payment_id=payment_id,
            status="CONFIRMED",
            amount=value,
            created_at=datetime.utcnow(),
        ))
        await session.flush()

        user = await get_or_create_user_in_session(session, telegram_id)

        credits_row = await session.scalar(
            select(UserCredits).where(UserCredits.user_id == user.id)
        )
        if credits_row:
            new_balance = credits_row.credits_balance + credits
            credits_row.credits_balance = new_balance
            credits_row.updated_at = datetime.utcnow()
        else:
            new_balance = credits
            session.add(UserCredits(
                user_id=user.id,
                credits_balance=new_balance,
                updated_at=datetime.utcnow(),
            ))

        session.add(CreditTransaction(
            user_id=user.id,
            amount=credits,
            type="add",
            reference=payment_id,
            created_at=datetime.utcnow(),
        ))

        await session.commit()

    print(f"[CREDIT] +{credits} user={telegram_id} saldo={new_balance}")
    log_event(
        logger, "credits_added",
        telegram_id=telegram_id,
        credits=credits,
        payment_id=payment_id,
    )
    return True


async def consume_credit(telegram_id: str) -> bool:
    """
    Atomically decrements credits_balance by 1.
    Returns False if user has no credits or does not exist.
    """
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_id)
        )
        if not user:
            return False

        result = await session.execute(
            update(UserCredits)
            .where(UserCredits.user_id == user.id)
            .where(UserCredits.credits_balance >= 1)
            .values(
                credits_balance=UserCredits.credits_balance - 1,
                updated_at=datetime.utcnow(),
            )
        )
        if result.rowcount == 0:
            return False

        session.add(CreditTransaction(
            user_id=user.id,
            amount=-1,
            type="consume",
            created_at=datetime.utcnow(),
        ))
        await session.commit()

        balance_row = await session.scalar(
            select(UserCredits).where(UserCredits.user_id == user.id)
        )
        new_balance = balance_row.credits_balance if balance_row else 0
        print(f"[CREDIT] -1 user={telegram_id} saldo={new_balance}")

    log_event(logger, "credit_consumed", telegram_id=telegram_id)
    return True


async def refund_credit(telegram_id: str) -> None:
    """
    Best-effort credit refund after a failed image request creation.
    Adds 1 credit back; does not restore any specific transaction.
    """
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_id)
        )
        if not user:
            return
        credits_row = await session.scalar(
            select(UserCredits).where(UserCredits.user_id == user.id)
        )
        if credits_row:
            credits_row.credits_balance += 1
            credits_row.updated_at = datetime.utcnow()
        else:
            session.add(UserCredits(
                user_id=user.id,
                credits_balance=1,
                updated_at=datetime.utcnow(),
            ))
        session.add(CreditTransaction(
            user_id=user.id,
            amount=1,
            type="refund",
            created_at=datetime.utcnow(),
        ))
        await session.commit()
    log_event(logger, "credit_refunded", telegram_id=telegram_id)


async def get_admin_credits(limit: int = 20) -> list[dict]:
    """Returns users with positive credits_balance, sorted by balance descending."""
    async with SessionLocal() as session:
        stmt = (
            select(UserCredits, User)
            .join(User, User.id == UserCredits.user_id)
            .where(UserCredits.credits_balance > 0)
            .order_by(UserCredits.credits_balance.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
    return [
        {
            "telegram_id": user.telegram_user_id,
            "credits_balance": uc.credits_balance,
            "updated_at": uc.updated_at.strftime("%d/%m %H:%M"),
        }
        for uc, user in rows
    ]
