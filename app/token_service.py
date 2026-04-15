import secrets
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActivationToken
from app.observability import get_logger, log_event


logger = get_logger(__name__)


def generate_token_value() -> str:
    return secrets.token_urlsafe(16)


async def create_activation_token() -> str:
    token_value = generate_token_value()

    async with SessionLocal() as session:
        row = ActivationToken(
            token=token_value,
            status="pending",
        )
        session.add(row)
        await session.commit()

    log_event(logger, "activation_token_created")
    return token_value


async def validate_activation_token(token_value: str):
    async with SessionLocal() as session:
        stmt = select(ActivationToken).where(ActivationToken.token == token_value)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def consume_activation_token(token_value: str, telegram_user_id: str) -> bool:
    async with SessionLocal() as session:
        stmt = select(ActivationToken).where(ActivationToken.token == token_value)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()

        if not row:
            return False

        if row.status != "pending":
            return False

        row.status = "used"
        row.telegram_user_id = telegram_user_id

        await session.commit()
        log_event(logger, "activation_token_consumed", telegram_user_id=telegram_user_id)
        return True
