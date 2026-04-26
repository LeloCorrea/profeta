from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select

from app.config import ASAAS_API_KEY, ASAAS_PAYMENT_LINK_ID, ASAAS_PAYMENT_LINK_URL, ASAAS_SUBSCRIPTION_VALUE, BOT_USERNAME, CURRENT_TENANT, PUBLIC_BASE_URL
from app.db import SessionLocal
from app.models import ActivationToken, Payment, Subscription, User
from app.observability import get_logger, log_event
from app.tenant_config import TenantConfig
from app.token_service import generate_token_value

PAYMENT_LINK_ID = ASAAS_PAYMENT_LINK_ID

logger = get_logger(__name__)


async def create_token_for_paid_event(
    asaas_payment_id: Optional[str],
    asaas_customer_id: Optional[str],
    asaas_payment_link_id: Optional[str],
    value: Optional[float] = None,
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
            amount=value,
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
        # Fail closed: missing config must never silently accept any payment.
        log_event(
            logger,
            "payment_link_misconfigured",
            reason="ASAAS_PAYMENT_LINK_ID not set — rejecting link-based payment",
            payment_link_id=link_id or "",
            level=40,
        )
        return False
    return (link_id or "").strip() == PAYMENT_LINK_ID


async def save_payment_idempotent(
    asaas_payment_id: str,
    asaas_customer_id: Optional[str],
    value: Optional[float] = None,
) -> bool:
    """
    Saves the confirmed payment for idempotency. Returns False if already processed.
    Used in the direct-activation flow (externalReference) where no token is needed.
    """
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(Payment).where(Payment.provider_payment_id == asaas_payment_id)
        )
        if existing:
            log_event(logger, "payment_already_processed", provider_payment_id=asaas_payment_id)
            return False

        payment = Payment(
            provider="asaas",
            provider_payment_id=asaas_payment_id,
            status="CONFIRMED",
            amount=value,
            customer_id=asaas_customer_id,
            created_at=datetime.utcnow(),
        )
        session.add(payment)
        await session.commit()
        log_event(logger, "payment_saved", provider_payment_id=asaas_payment_id)
        return True


async def create_payment_for_user(
    telegram_user_id: str,
    full_name: Optional[str] = None,
) -> dict:
    """
    Creates a per-user PIX payment on Asaas with externalReference=telegram_user_id.
    Returns {invoice_url, pix_code, value, fallback} where fallback=True means
    the API call failed and the static payment link is being returned instead.
    """
    from app.asaas_client import create_pix_payment, get_or_create_customer, get_pix_qr_code

    if not ASAAS_API_KEY:
        return {"invoice_url": ASAAS_PAYMENT_LINK_URL, "pix_code": None, "value": None, "fallback": True}

    name = (full_name or "").strip() or f"Profeta {telegram_user_id}"
    customer_id = await get_or_create_customer(telegram_user_id, name)
    if not customer_id:
        return {"invoice_url": ASAAS_PAYMENT_LINK_URL, "pix_code": None, "value": None, "fallback": True}

    payment = await create_pix_payment(
        customer_id=customer_id,
        value=ASAAS_SUBSCRIPTION_VALUE,
        external_reference=telegram_user_id,
    )
    if not payment:
        return {"invoice_url": ASAAS_PAYMENT_LINK_URL, "pix_code": None, "value": None, "fallback": True}

    pix_code = await get_pix_qr_code(payment["payment_id"])

    log_event(
        logger,
        "payment_created_for_user",
        telegram_user_id=telegram_user_id,
        payment_id=payment["payment_id"],
        has_pix_code=pix_code is not None,
    )

    return {
        "invoice_url": payment["invoice_url"],
        "pix_code": pix_code,
        "value": ASAAS_SUBSCRIPTION_VALUE,
        "fallback": False,
    }


async def create_credit_payment(
    telegram_user_id: str,
    credits: int,
    price: float,
    full_name: Optional[str] = None,
) -> dict:
    """
    Creates a PIX payment for a credit package purchase.
    externalReference = "credits:{telegram_user_id}:{credits}" — parsed by the webhook.
    Returns {invoice_url, pix_code, value, fallback} where fallback=True means API failure.
    """
    from app.asaas_client import create_pix_payment, get_or_create_customer, get_pix_qr_code

    external_reference = f"credits:{telegram_user_id}:{credits}"

    if not ASAAS_API_KEY:
        return {"invoice_url": None, "pix_code": None, "value": price, "fallback": True}

    name = (full_name or "").strip() or f"Profeta {telegram_user_id}"
    customer_id = await get_or_create_customer(telegram_user_id, name)
    if not customer_id:
        return {"invoice_url": None, "pix_code": None, "value": price, "fallback": True}

    payment = await create_pix_payment(
        customer_id=customer_id,
        value=price,
        external_reference=external_reference,
    )
    if not payment:
        return {"invoice_url": None, "pix_code": None, "value": price, "fallback": True}

    pix_code = await get_pix_qr_code(payment["payment_id"])

    log_event(
        logger,
        "credit_payment_created",
        telegram_user_id=telegram_user_id,
        credits=credits,
        price=price,
        payment_id=payment["payment_id"],
    )

    return {
        "invoice_url": payment["invoice_url"],
        "pix_code": pix_code,
        "value": price,
        "fallback": False,
    }


async def activate_with_payment_atomic(
    asaas_payment_id: str,
    asaas_customer_id: Optional[str],
    telegram_user_id: str,
    value: Optional[float] = None,
) -> bool:
    """
    Atomically: idempotency check → Payment → User upsert → Subscription activation.
    All writes happen in a single session/transaction.
    Returns False if already processed (idempotent). Raises on DB failure (allows Asaas retry).
    """
    async with SessionLocal() as session:
        # 1. Idempotency — fail fast before any write
        existing = await session.scalar(
            select(Payment).where(Payment.provider_payment_id == asaas_payment_id)
        )
        if existing:
            log_event(logger, "payment_already_processed", provider_payment_id=asaas_payment_id)
            return False

        # 2. Get or create User
        user = await session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if not user:
            user = User(telegram_user_id=telegram_user_id, status="active")
            session.add(user)
            await session.flush()  # populate user.id before FK reference

        if asaas_customer_id and user.asaas_customer_id != asaas_customer_id:
            if user.asaas_customer_id:
                log_event(logger, "customer_id_updated", telegram_user_id=telegram_user_id,
                          previous=user.asaas_customer_id, new=asaas_customer_id)
            user.asaas_customer_id = asaas_customer_id

        # 3. Create Payment record
        session.add(Payment(
            provider="asaas",
            provider_payment_id=asaas_payment_id,
            status="CONFIRMED",
            amount=value,
            customer_id=asaas_customer_id,
            created_at=datetime.utcnow(),
        ))

        # 4. Activate or renew Subscription
        now = datetime.utcnow()
        sub = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        if sub:
            base = sub.paid_until if (sub.paid_until and sub.paid_until > now) else now
            sub.paid_until = base + timedelta(days=30)
            sub.status = "active"
        else:
            session.add(Subscription(
                user_id=user.id,
                plan_name="monthly",
                status="active",
                paid_until=now + timedelta(days=30),
            ))

        # 5. Single commit — atomically persists all of the above
        await session.commit()

    log_event(
        logger,
        "payment_confirmed_and_subscription_activated",
        provider_payment_id=asaas_payment_id,
        telegram_user_id=telegram_user_id,
    )
    return True


async def find_telegram_user_for_customer(asaas_customer_id: str) -> Optional[str]:
    """Return telegram_user_id for a known Asaas customer, or None if not mapped yet."""
    if not asaas_customer_id:
        return None
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.asaas_customer_id == asaas_customer_id)
        )
    return user.telegram_user_id if user else None
