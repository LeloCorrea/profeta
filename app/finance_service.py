from datetime import datetime

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import CreditTransaction, Payment, User, UserCredits


async def get_finance_summary() -> dict:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    async with SessionLocal() as session:
        total_payments = await session.scalar(select(func.count(Payment.id))) or 0

        total_revenue = await session.scalar(
            select(func.sum(Payment.amount)).where(Payment.status == "CONFIRMED")
        ) or 0.0

        revenue_today = await session.scalar(
            select(func.sum(Payment.amount))
            .where(Payment.status == "CONFIRMED")
            .where(Payment.created_at >= today_start)
        ) or 0.0

        credit_add_rows = (await session.execute(
            select(CreditTransaction.amount).where(CreditTransaction.type == "add")
        )).scalars().all()

        credits_consumed = await session.scalar(
            select(func.sum(func.abs(CreditTransaction.amount)))
            .where(CreditTransaction.type == "consume")
        ) or 0
        balance_total = await session.scalar(select(func.sum(UserCredits.credits_balance))) or 0
        users_with_credits = await session.scalar(
            select(func.count(UserCredits.id)).where(UserCredits.credits_balance > 0)
        ) or 0

    return {
        "total_revenue": round(float(total_revenue), 2),
        "revenue_today": round(float(revenue_today), 2),
        "total_payments": total_payments,
        "credits_sold": sum(credit_add_rows),
        "credits_consumed": int(credits_consumed),
        "credits_balance_total": int(balance_total),
        "total_users_with_credits": int(users_with_credits),
    }


async def get_credit_transactions_list(limit: int = 50) -> list[dict]:
    async with SessionLocal() as session:
        stmt = (
            select(CreditTransaction, User)
            .join(User, User.id == CreditTransaction.user_id)
            .order_by(CreditTransaction.created_at.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
    return [
        {
            "user_id": user.telegram_user_id,
            "type": ct.type,
            "amount": ct.amount,
            "reference": ct.reference or "",
            "created_at": ct.created_at.strftime("%d/%m/%Y %H:%M"),
        }
        for ct, user in rows
    ]


async def get_payments_list(limit: int = 50) -> list[dict]:
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Payment).order_by(Payment.created_at.desc()).limit(limit)
        )).scalars().all()
    return [
        {
            "payment_id": p.provider_payment_id,
            "customer_id": p.customer_id or "",
            "value": p.amount,
            "status": p.status or "",
            "created_at": p.created_at.strftime("%d/%m/%Y %H:%M"),
        }
        for p in rows
    ]
