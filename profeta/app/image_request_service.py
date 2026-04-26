from typing import Optional

from sqlalchemy import func, select, update

from app.config import IMAGE_PRICE
from app.db import SessionLocal
from app.db_helpers import get_or_create_user_in_session
from app.models import ImageRequest
from app.observability import get_logger, log_event

logger = get_logger(__name__)


async def create_image_request(
    session_factory,
    telegram_id: str,
    content_type: str,
    content_text: str,
    price: float = IMAGE_PRICE,
    payment_status: str = "pending_payment",
) -> ImageRequest:
    async with session_factory() as session:
        user = await get_or_create_user_in_session(session, telegram_id)
        req = ImageRequest(
            user_id=user.id,
            telegram_id=telegram_id,
            content_type=content_type,
            content_text=content_text,
            status="pending",
            price=price,
            payment_status=payment_status,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        log_event(
            logger,
            "image_request_created",
            telegram_id=telegram_id,
            content_type=content_type,
            request_id=req.id,
            price=price,
        )
        return req


async def get_admin_image_requests(
    limit: int = 20,
    payment_status: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    async with SessionLocal() as session:
        stmt = select(ImageRequest).order_by(ImageRequest.created_at.desc()).limit(limit)
        if payment_status:
            stmt = stmt.where(ImageRequest.payment_status == payment_status)
        if status:
            stmt = stmt.where(ImageRequest.status == status)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "telegram_id": r.telegram_id,
            "content_type": r.content_type,
            "price": r.price,
            "status": r.status,
            "payment_status": r.payment_status,
            "created_at": r.created_at.strftime("%d/%m %H:%M"),
        }
        for r in rows
    ]


async def mark_request_processing(request_id: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(ImageRequest)
            .where(ImageRequest.id == request_id)
            .values(status="processing")
        )
        await session.commit()
    log_event(logger, "image_request_processing", request_id=request_id)


async def mark_request_done(request_id: int, image_path: Optional[str] = None) -> None:
    values: dict = {"status": "done"}
    if image_path:
        values["image_path"] = image_path
    async with SessionLocal() as session:
        await session.execute(
            update(ImageRequest).where(ImageRequest.id == request_id).values(**values)
        )
        await session.commit()
    log_event(logger, "image_request_done", request_id=request_id)


async def mark_request_paid(request_id: int, telegram_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(ImageRequest)
            .where(ImageRequest.id == request_id)
            .values(payment_status="paid")
        )
        await session.commit()
    log_event(logger, "image_request_paid", request_id=request_id, telegram_id=telegram_id)
    await _notify_image_payment_confirmed(telegram_id)


async def _notify_image_payment_confirmed(telegram_id: str) -> None:
    from app.config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=telegram_id,
            text="Pagamento confirmado 🙏\nSua imagem será entregue em instantes.",
        )
    except Exception as err:
        log_event(
            logger,
            "image_payment_notification_failed",
            telegram_id=telegram_id,
            error=str(err)[:200],
        )


async def count_pending_requests(telegram_id: str) -> int:
    """Returns the number of ImageRequests with status='pending' for the given user."""
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count(ImageRequest.id))
            .where(ImageRequest.telegram_id == telegram_id)
            .where(ImageRequest.status == "pending")
        )
    return count or 0


async def get_paid_pending_requests(request_id: Optional[int] = None) -> list[ImageRequest]:
    async with SessionLocal() as session:
        if request_id is not None:
            stmt = select(ImageRequest).where(ImageRequest.id == request_id)
        else:
            stmt = (
                select(ImageRequest)
                .where(ImageRequest.payment_status == "paid")
                .where(ImageRequest.status == "pending")
                .order_by(ImageRequest.created_at.asc())
            )
        return list((await session.execute(stmt)).scalars().all())
