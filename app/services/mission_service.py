"""Gerencia missões diárias de engajamento espiritual."""
from datetime import date, datetime
from typing import Optional

from sqlalchemy import and_, select

from app.db import SessionLocal
from app.models import Subscription, User, UserMission
from app.observability import get_logger

logger = get_logger(__name__)


async def get_or_create_user_mission(telegram_user_id: str, mission_type: str = "reflection") -> UserMission:
    """Retorna a missão de hoje para o usuário, criando se não existir."""
    today = date.today()
    async with SessionLocal() as session:
        mission = await session.scalar(
            select(UserMission).where(
                and_(
                    UserMission.telegram_user_id == str(telegram_user_id),
                    UserMission.assigned_date == today,
                )
            )
        )
        if not mission:
            mission = UserMission(
                telegram_user_id=str(telegram_user_id),
                mission_type=mission_type,
                status="pending",
                assigned_date=today,
            )
            session.add(mission)
            await session.commit()
            await session.refresh(mission)
        return mission


async def get_users_with_pending_mission() -> list[str]:
    """Retorna telegram_user_ids de assinantes ativos com missão pendente hoje."""
    today = date.today()
    now = datetime.utcnow()

    async with SessionLocal() as session:
        active_stmt = (
            select(User.telegram_user_id)
            .join(Subscription, Subscription.user_id == User.id)
            .where(Subscription.status == "active")
            .where(Subscription.paid_until > now)
        )
        active_result = await session.execute(active_stmt)
        active_ids: set[str] = {row[0] for row in active_result.all() if row[0]}

        if not active_ids:
            return []

        pending_stmt = (
            select(UserMission.telegram_user_id)
            .where(UserMission.status == "pending")
            .where(UserMission.assigned_date == today)
        )
        pending_result = await session.execute(pending_stmt)
        pending_ids: set[str] = {row[0] for row in pending_result.all() if row[0]}

    return sorted(pending_ids & active_ids)


async def create_daily_mission(telegram_user_id: str, mission_type: str = "reflection") -> None:
    """Atribui missão diária ao usuário se ainda não existir para hoje."""
    today = date.today()
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(UserMission).where(
                and_(
                    UserMission.telegram_user_id == str(telegram_user_id),
                    UserMission.assigned_date == today,
                )
            )
        )
        if not existing:
            session.add(
                UserMission(
                    telegram_user_id=str(telegram_user_id),
                    mission_type=mission_type,
                    status="pending",
                    assigned_date=today,
                )
            )
            await session.commit()


async def complete_mission(telegram_user_id: str) -> Optional[int]:
    """Marca a missão de hoje como concluída. Retorna o ID da missão ou None."""
    today = date.today()
    async with SessionLocal() as session:
        mission = await session.scalar(
            select(UserMission).where(
                and_(
                    UserMission.telegram_user_id == str(telegram_user_id),
                    UserMission.assigned_date == today,
                    UserMission.status == "pending",
                )
            )
        )
        if mission:
            mission.status = "completed"
            mission.completed_at = datetime.utcnow()
            await session.commit()
            return mission.id
    return None
