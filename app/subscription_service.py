from sqlalchemy import select

from app.db import SessionLocal
from app.models import User, Subscription


async def get_or_create_user(telegram_user_id: str, telegram_username: str | None = None, full_name: str | None = None) -> User:
    async with SessionLocal() as session:
        stmt = select(User).where(User.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if user:
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
        return user


async def activate_subscription_for_user(user_id: int) -> Subscription:
    async with SessionLocal() as session:
        stmt = select(Subscription).where(Subscription.user_id == user_id)
        result = await session.execute(stmt)
        sub = result.scalar_one_or_none()

        if sub:
            sub.status = "active"
            await session.commit()
            await session.refresh(sub)
            return sub

        sub = Subscription(
            user_id=user_id,
            plan_name="monthly",
            status="active",
            paid_until=None,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
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
        return sub is not None
