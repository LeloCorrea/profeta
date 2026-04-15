import os
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActivationToken, Payment
from app.observability import get_logger, log_event
from app.token_service import generate_token_value

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
PAYMENT_LINK_ID = os.getenv("ASAAS_PAYMENT_LINK_ID", "")

logger = get_logger(__name__)


async def create_token_for_paid_event(
    asaas_payment_id: Optional[str],
    asaas_customer_id: Optional[str],
    asaas_payment_link_id: Optional[str],
) -> Optional[str]:

    if not asaas_payment_id:
        return None

    async with SessionLocal() as session:

        # 🔒 1. IDEMPOTÊNCIA — verificar se já existe payment
        existing_payment = await session.scalar(
            select(Payment).where(
                Payment.provider_payment_id == asaas_payment_id
            )
        )

        if existing_payment:
            # já processado → NÃO cria novo token
            log_event(logger, "payment_already_processed", provider_payment_id=asaas_payment_id)
            return None

        # ✅ 2. SALVAR PAYMENT
        payment = Payment(
            provider="asaas",
            provider_payment_id=asaas_payment_id,
            payment_link_id=asaas_payment_link_id,
            status="CONFIRMED",
            customer_id=asaas_customer_id,
            created_at=datetime.utcnow(),
        )

        session.add(payment)
        await session.flush()

        # 🔐 3. GERAR TOKEN NA MESMA SESSÃO PARA EVITAR LOCK NO SQLITE
        token = generate_token_value()
        row = ActivationToken(
            token=token,
            status="pending",
            asaas_payment_id=asaas_payment_id,
            asaas_customer_id=asaas_customer_id,
            asaas_payment_link_id=asaas_payment_link_id,
        )
        session.add(row)

        await session.commit()

        log_event(
            logger,
            "payment_confirmed_and_token_created",
            provider_payment_id=asaas_payment_id,
            payment_link_id=asaas_payment_link_id or "",
        )

        return token


def build_telegram_start_link(token: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start={quote(token)}"


def build_claim_url(token: str) -> str:
    return f"{PUBLIC_BASE_URL}/claim/{quote(token)}"


def payment_link_matches(link_id: Optional[str]) -> bool:
    if not PAYMENT_LINK_ID:
        return True
    return (link_id or "").strip() == PAYMENT_LINK_ID
