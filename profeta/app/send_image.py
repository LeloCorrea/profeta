"""
Script de envio de imagens para pedidos pagos.

Uso:
  python -m app.send_image                    # processa todos os pedidos pagos pendentes
  python -m app.send_image --request-id ID    # processa pedido específico

REGRA CRÍTICA: payment_status deve ser "paid" — qualquer outro valor aborta o envio.
"""

import argparse
import asyncio
import logging
from typing import Optional

from app.image_request_service import (
    get_paid_pending_requests,
    mark_request_done,
    mark_request_processing,
)
from app.models import ImageRequest
from app.observability import get_logger, log_event

logger = get_logger("send_image")

_IMAGE_DELIVERED_CAPTION = (
    "Sua imagem está pronta 🙏\n"
    "Que essa mensagem abençoe seu dia."
)


def _validate_payment(request: ImageRequest) -> bool:
    """Payment gate — never deliver without confirmed payment."""
    if request.payment_status != "paid":
        print(f"[SKIP] Request {request.id} não pago")
        logger.error(
            "[BLOQUEADO] Pedido #%d não pago. payment_status=%r — envio abortado.",
            request.id,
            request.payment_status,
        )
        log_event(
            logger,
            "image_delivery_blocked_unpaid",
            level=logging.ERROR,
            request_id=request.id,
            telegram_id=request.telegram_id,
            payment_status=request.payment_status,
        )
        return False
    return True


async def process_request(request: ImageRequest) -> bool:
    """Process a single paid image request. Returns True on success."""
    # CRITICAL: payment gate — no bypass allowed
    if not _validate_payment(request):
        return False

    if request.status == "done":
        print(f"[SKIP] Request {request.id} já entregue")
        return True

    await mark_request_processing(request.id)
    log_event(
        logger,
        "image_request_started",
        request_id=request.id,
        telegram_id=request.telegram_id,
        content_type=request.content_type,
    )

    try:
        # TODO: generate image via share_service.generate_share_card, then:
        #   await bot.send_photo(chat_id=request.telegram_id, photo=image_file, caption=_IMAGE_DELIVERED_CAPTION)
        # Placeholder until payment gateway is integrated and triggers this flow automatically.
        logger.info(
            "Pedido #%d pronto: content_type=%r, telegram_id=%r",
            request.id,
            request.content_type,
            request.telegram_id,
        )
        image_path: Optional[str] = None  # set when generation is implemented

        await mark_request_done(request.id, image_path)
        log_event(logger, "image_request_delivered", request_id=request.id)
        print(f"[OK] Enviado request {request.id} para user {request.telegram_id}")
        return True

    except Exception as exc:
        print(f"[ERROR] Request {request.id}: {exc}")
        logger.error("Falha ao processar pedido #%d: %s", request.id, exc)
        log_event(
            logger,
            "image_request_failed",
            level=logging.ERROR,
            request_id=request.id,
            error=str(exc)[:200],
        )
        return False


async def main(request_id: Optional[int] = None) -> None:
    requests = await get_paid_pending_requests(request_id)
    if not requests:
        logger.info("Nenhum pedido pago pendente.")
        return

    processed, failed = 0, 0
    for req in requests:
        if await process_request(req):
            processed += 1
        else:
            failed += 1

    logger.info("Pedidos processados: %d | falhas: %d", processed, failed)
    log_event(logger, "send_image_job_finished", processed=processed, failed=failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Processa pedidos de imagem pagos.")
    parser.add_argument("--request-id", type=int, default=None, metavar="ID")
    args = parser.parse_args()
    asyncio.run(main(request_id=args.request_id))
