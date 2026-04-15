from sqlalchemy import select
from typing import Optional, Union

from app.db import SessionLocal
from app.models import User, Subscription
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

        if sub:
            sub.status = "active"
            await session.commit()
            await session.refresh(sub)
            log_event(logger, "subscription_activated", telegram_user_id=user.telegram_user_id, reused=True)
            return sub

        sub = Subscription(
            user_id=user.id,
            plan_name="monthly",
            status="active",
            paid_until=None,
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
