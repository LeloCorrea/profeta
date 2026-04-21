from datetime import datetime, timedelta
from typing import Optional, Union

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Subscription, User
from app.observability import get_logger, log_event


logger = get_logger(__name__)


async def get_or_create_user(
    telegram_user_id: str,
    telegram_username: Optional[str] = None,
    full_name: Optional[str] = None,
) -> User:
    async with SessionLocal() as session:
        stmt = select(User).where(User.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user:
            log_event(logger, "user_loaded", telegram_user_id=telegram_user_id)
            return user

        user = User(
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            full_name=full_name,
            status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        log_event(logger, "user_created", telegram_user_id=telegram_user_id)
        return user


async def activate_subscription_for_user(
    user_id: Optional[Union[int, str]] = None,
    telegram_user_id: Optional[str] = None,
) -> Subscription:
    async with SessionLocal() as session:
        user = None
        if telegram_user_id is not None:
            user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        elif isinstance(user_id, int):
            user = await session.scalar(select(User).where(User.id == user_id))
        elif isinstance(user_id, str):
            user = await session.scalar(select(User).where(User.telegram_user_id == user_id))
            if not user and user_id.isdigit():
                user = await session.scalar(select(User).where(User.id == int(user_id)))

        if not user:
            raise ValueError("Usuário não encontrado para ativação de assinatura.")

        stmt = select(Subscription).where(Subscription.user_id == user.id)
        result = await session.execute(stmt)
        sub = result.scalar_one_or_none()

        now = datetime.utcnow()
        if sub:
            # Renewal: extend from current expiry if future, else from now
            if sub.paid_until and sub.paid_until > now:
                sub.paid_until = sub.paid_until + timedelta(days=30)
            else:
                sub.paid_until = now + timedelta(days=30)
            sub.status = "active"
            await session.commit()
            await session.refresh(sub)
            log_event(logger, "subscription_activated", telegram_user_id=user.telegram_user_id, reused=True)
            return sub

        sub = Subscription(
            user_id=user.id,
            plan_name="monthly",
            status="active",
            paid_until=now + timedelta(days=30),
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        log_event(logger, "subscription_activated", telegram_user_id=user.telegram_user_id, reused=False)
        return sub


async def user_has_active_subscription(telegram_user_id: str) -> bool:
    async with SessionLocal() as session:
        stmt = (
            select(Subscription)
            .join(User, User.id == Subscription.user_id)
            .where(User.telegram_user_id == telegram_user_id)
            .where(Subscription.status == "active")
        )
        result = await session.execute(stmt)
        sub = result.scalar_one_or_none()
        log_event(
            logger,
            "subscription_checked",
            telegram_user_id=telegram_user_id,
            has_active_subscription=sub is not None,
        )
        return sub is not None


async def expire_overdue_subscriptions() -> int:
    now = datetime.utcnow()
    async with SessionLocal() as session:
        stmt = (
            select(Subscription)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until.isnot(None))
            .where(Subscription.paid_until < now)
        )
        rows = (await session.execute(stmt)).scalars().all()
        for sub in rows:
            sub.status = "inactive"
        await session.commit()

    count = len(rows)
    if count:
        log_event(logger, "subscriptions_expired", count=count)
    return count


async def get_admin_stats() -> dict:
    async with SessionLocal() as session:
        total_users = (await session.execute(
            select(func.count()).select_from(User)
        )).scalar_one() or 0
        active_subs = (await session.execute(
            select(func.count()).select_from(Subscription)
            .where(Subscription.status == "active")
        )).scalar_one() or 0
    return {"total_users": total_users, "active_subscriptions": active_subs}


async def get_subscription_info(telegram_user_id: str) -> dict:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
        if not user:
            return {"has_account": False}
        sub = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc())
        )
        if not sub:
            return {"has_account": True, "has_subscription": False}
        days_remaining = None
        if sub.paid_until:
            delta = sub.paid_until - datetime.utcnow()
            days_remaining = max(0, delta.days)
        return {
            "has_account": True,
            "has_subscription": True,
            "status": sub.status,
            "plan": sub.plan_name,
            "paid_until": sub.paid_until.strftime("%d/%m/%Y") if sub.paid_until else None,
            "days_remaining": days_remaining,
        }


async def get_admin_recent_users(limit: int = 10) -> list[dict]:
    async with SessionLocal() as session:
        stmt = (
            select(User)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
            .order_by(User.created_at.desc())
            .limit(limit)
        )
        users = (await session.execute(stmt)).scalars().all()
    return [
        {
            "telegram_user_id": u.telegram_user_id,
            "username": u.telegram_username or "—",
            "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else "—",
        }
        for u in users
    ]
