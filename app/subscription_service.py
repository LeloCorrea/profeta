import logging
from datetime import datetime, timedelta
from typing import Optional, Union

from sqlalchemy import func, select

from app.config import is_admin
from app.db import SessionLocal
from app.models import Subscription, User
from app.observability import get_logger, log_event


logger = get_logger(__name__)


def normalize_user_id(user_id) -> str:
    """Garante que o telegram_user_id é sempre string antes de qualquer query."""
    return str(user_id)


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


async def user_has_active_subscription(telegram_user_id) -> bool:
    """Verifica apenas se existe assinatura ativa. Não decide acesso sozinha."""
    uid = normalize_user_id(telegram_user_id)
    now = datetime.utcnow()
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == uid))
        if not user:
            log_event(logger, "subscription_checked", telegram_user_id=uid,
                      has_active_subscription=False, reason="user_not_found")
            return False
        sub = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until > now)
        )
    log_event(logger, "subscription_checked", telegram_user_id=uid,
              has_active_subscription=sub is not None)
    return sub is not None


async def user_has_access(telegram_user_id) -> bool:
    """Ponto único de autorização. Ordem de verificação:

    1. Admin de config (ADMIN_TELEGRAM_IDS) — fast-path, sem DB
    2. Usuário carregado do DB → role == 'super_admin'
    3. Assinatura ativa
    """
    uid = normalize_user_id(telegram_user_id)

    # Fast-path: admin definido no env (sem query ao banco)
    if is_admin(uid):
        log_event(logger, "access_granted", telegram_user_id=uid, reason="config_admin")
        return True

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == uid))

    if not user:
        log_event(logger, "access_denied", telegram_user_id=uid, reason="user_not_found")
        return False

    log_event(logger, "user_loaded", telegram_user_id=uid, role=user.role)

    if user.role == "super_admin":
        log_event(logger, "super_admin_detected", telegram_user_id=uid)
        log_event(logger, "access_granted", telegram_user_id=uid, reason="super_admin")
        return True

    has_sub = await user_has_active_subscription(uid)
    if has_sub:
        log_event(logger, "access_granted", telegram_user_id=uid, reason="active_subscription")
    else:
        log_event(logger, "access_denied", telegram_user_id=uid, reason="no_active_subscription")
    return has_sub


async def expire_overdue_subscriptions() -> tuple[int, list[str]]:
    now = datetime.utcnow()
    async with SessionLocal() as session:
        stmt = (
            select(Subscription, User.telegram_user_id)
            .join(User, User.id == Subscription.user_id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until.isnot(None))
            .where(Subscription.paid_until < now)
        )
        rows = (await session.execute(stmt)).all()
        user_ids: list[str] = []
        for sub, telegram_user_id in rows:
            sub.status = "inactive"
            if telegram_user_id:
                user_ids.append(telegram_user_id)
        await session.commit()

    count = len(rows)
    if count:
        log_event(logger, "subscriptions_expired", count=count)
    return count, user_ids


async def get_users_expiring_in_window(days_min: int, days_max: int) -> list[str]:
    """Retorna IDs de usuários com assinatura expirando entre days_min e days_max dias a partir de agora."""
    now = datetime.utcnow()
    window_start = now + timedelta(days=days_min)
    window_end = now + timedelta(days=days_max)
    async with SessionLocal() as session:
        stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until.isnot(None))
            .where(Subscription.paid_until > window_start)
            .where(Subscription.paid_until <= window_end)
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all() if row[0]]


async def get_admin_stats() -> dict:
    now = datetime.utcnow()
    week_ahead = now + timedelta(days=7)
    async with SessionLocal() as session:
        total_users = (await session.execute(
            select(func.count()).select_from(User)
        )).scalar_one() or 0
        active_subs = (await session.execute(
            select(func.count()).select_from(Subscription)
            .where(Subscription.status == "active")
        )).scalar_one() or 0
        expiring_7d = (await session.execute(
            select(func.count()).select_from(Subscription)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until.isnot(None))
            .where(Subscription.paid_until > now)
            .where(Subscription.paid_until <= week_ahead)
        )).scalar_one() or 0
    return {
        "total_users": total_users,
        "active_subscriptions": active_subs,
        "expiring_7_days": expiring_7d,
    }


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


async def record_user_interaction(telegram_user_id: str) -> bool:
    """Update last_interaction_at for dormancy tracking.

    Fire-and-forget: never raises. Returns True if the update was persisted,
    False if the DB write failed (caller may act on this if needed).
    """
    try:
        async with SessionLocal() as session:
            user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
            if user:
                user.last_interaction_at = datetime.utcnow()
                await session.commit()
        return True
    except Exception as exc:
        log_event(
            logger, "record_interaction_failed", level=logging.WARNING,
            telegram_user_id=telegram_user_id,
            error_type=type(exc).__name__, error=str(exc)[:200],
        )
        return False


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
