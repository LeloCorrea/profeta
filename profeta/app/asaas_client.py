"""
Asaas API client — async, minimal, focused on payment creation.

Supports:
- get_or_create_customer: idempotent customer lookup by externalReference
- create_pix_payment: creates a PIX payment with externalReference=telegram_user_id
- get_pix_qr_code: retrieves PIX copy-paste payload for a payment
"""
from datetime import datetime, timedelta
from typing import Optional

import httpx

from app.config import ASAAS_API_KEY, ASAAS_BASE_URL, CURRENT_TENANT
from app.observability import get_logger, log_event
from app.tenant_config import TenantConfig

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(15.0)


def _headers(api_key: str) -> dict:
    return {"access_token": api_key, "Content-Type": "application/json"}


async def get_or_create_customer(
    telegram_user_id: str,
    name: str,
    cfg: Optional[TenantConfig] = None,
) -> Optional[str]:
    """
    Returns Asaas customer ID for the given telegram_user_id.
    Looks up by externalReference first; creates if not found.
    Returns None if the API call fails.
    """
    _cfg = cfg or CURRENT_TENANT
    api_key = _cfg.secrets.asaas_api_key or ASAAS_API_KEY
    base_url = _cfg.asaas_base_url

    if not api_key:
        return None

    app_name = _cfg.branding.app_name

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # 1. Look up existing customer
        try:
            resp = await client.get(
                f"{base_url}/customers",
                headers=_headers(api_key),
                params={"externalReference": telegram_user_id, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            existing = (data.get("data") or [])
            if existing:
                customer_id = existing[0]["id"]
                log_event(logger, "asaas_customer_found", telegram_user_id=telegram_user_id, customer_id=customer_id)
                return customer_id
        except Exception as err:
            log_event(logger, "asaas_customer_lookup_failed", telegram_user_id=telegram_user_id, error=str(err))
            return None

        # 2. Create new customer
        try:
            resp = await client.post(
                f"{base_url}/customers",
                headers=_headers(api_key),
                json={
                    "name": name or f"{app_name} {telegram_user_id}",
                    "externalReference": telegram_user_id,
                },
            )
            resp.raise_for_status()
            customer_id = resp.json()["id"]
            log_event(logger, "asaas_customer_created", telegram_user_id=telegram_user_id, customer_id=customer_id)
            return customer_id
        except Exception as err:
            log_event(logger, "asaas_customer_create_failed", telegram_user_id=telegram_user_id, error=str(err))
            return None


async def create_pix_payment(
    customer_id: str,
    value: float,
    external_reference: str,
    description: str = "",
    expiry_hours: int = 48,
    cfg: Optional[TenantConfig] = None,
) -> Optional[dict]:
    """
    Creates a PIX payment on Asaas.
    Returns dict with {payment_id, invoice_url} or None on failure.
    """
    _cfg = cfg or CURRENT_TENANT
    api_key = _cfg.secrets.asaas_api_key or ASAAS_API_KEY
    base_url = _cfg.asaas_base_url
    payment_desc = description or _cfg.branding.payment_description

    if not api_key:
        return None

    due_date = (datetime.utcnow() + timedelta(hours=expiry_hours)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{base_url}/payments",
                headers=_headers(api_key),
                json={
                    "billingType": "PIX",
                    "customer": customer_id,
                    "value": value,
                    "dueDate": due_date,
                    "externalReference": external_reference,
                    "description": payment_desc,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            log_event(
                logger,
                "asaas_payment_created",
                payment_id=data.get("id"),
                customer_id=customer_id,
                external_reference=external_reference,
            )
            return {
                "payment_id": data.get("id"),
                "invoice_url": data.get("invoiceUrl"),
            }
        except Exception as err:
            log_event(logger, "asaas_payment_create_failed", customer_id=customer_id, error=str(err))
            return None


async def get_pix_qr_code(
    payment_id: str,
    cfg: Optional[TenantConfig] = None,
) -> Optional[str]:
    """
    Returns the PIX copy-paste payload (copia e cola) for a payment, or None.
    """
    _cfg = cfg or CURRENT_TENANT
    api_key = _cfg.secrets.asaas_api_key or ASAAS_API_KEY
    base_url = _cfg.asaas_base_url

    if not api_key or not payment_id:
        return None

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{base_url}/payments/{payment_id}/pixQrCode",
                headers=_headers(api_key),
            )
            resp.raise_for_status()
            return resp.json().get("payload")
        except Exception as err:
            log_event(logger, "asaas_pix_qrcode_failed", payment_id=payment_id, error=str(err))
            return None
