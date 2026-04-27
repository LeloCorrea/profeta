from sqlalchemy import select

from app.models import User


async def get_or_create_user_in_session(session, telegram_user_id: str) -> User:
    user = await session.scalar(select(User).where(User.telegram_user_id == str(telegram_user_id)))
    if user:
        return user

    user = User(telegram_user_id=str(telegram_user_id), status="active")
    session.add(user)
    await session.flush()
    return user
