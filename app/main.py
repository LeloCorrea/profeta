import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.db import engine, Base
import app.models  # noqa: F401
from app.observability import get_logger, log_event
from app.payment_service import (
    build_claim_url,
    build_telegram_start_link,
    create_token_for_paid_event,
    payment_link_matches,
)

app = FastAPI(title="Profeta API")

ASAAS_WEBHOOK_TOKEN = os.getenv("ASAAS_WEBHOOK_TOKEN", "")
logger = get_logger(__name__)


@app.on_event("startup")
async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.get("/")
async def root():
    return {"ok": True, "app": "profeta"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/webhooks/asaas")
async def asaas_webhook(
    request: Request,
    asaas_access_token: str | None = Header(default=None),
):
    # 🔒 1. Segurança do webhook
    if not ASAAS_WEBHOOK_TOKEN:
        raise HTTPException(status_code=500, detail="ASAAS_WEBHOOK_TOKEN não configurado")

    if asaas_access_token != ASAAS_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Webhook token inválido")

    # 📦 2. Ler payload
    payload = await request.json()

    event = (payload.get("event") or "").strip()
    payment = payload.get("payment", {}) or {}

    payment_id = (payment.get("id") or "").strip()
    customer_id = (payment.get("customer") or "").strip()
    payment_link_id = (payment.get("paymentLink") or "").strip()
    status = (payment.get("status") or "").strip().upper()

    log_event(
        logger,
        "asaas_webhook_received",
        event_name=event,
        payment_id=payment_id,
        customer_id=customer_id,
        payment_link_id=payment_link_id,
        status=status,
    )

    # 🔒 3. Validação básica
    if not payment_id:
        log_event(logger, "asaas_webhook_ignored", reason="payment_id_missing")
        return {"ok": True, "ignored": "payment.id ausente"}

    if not payment_id.startswith("pay_"):
        log_event(logger, "asaas_webhook_ignored", reason="invalid_payment_id")
        return {"ok": True, "ignored": "payment_id inválido"}

    if not payment_link_id:
        log_event(logger, "asaas_webhook_ignored", reason="payment_link_missing")
        return {"ok": True, "ignored": "payment.paymentLink ausente"}

    # 🔒 4. Validar produto correto
    if not payment_link_matches(payment_link_id):
        log_event(logger, "asaas_webhook_ignored", reason="unexpected_payment_link")
        return {"ok": True, "ignored": "paymentLink diferente"}

    # 🔒 5. Validar evento correto
    if event != "PAYMENT_CONFIRMED":
        log_event(logger, "asaas_webhook_ignored", reason="irrelevant_event", event_name=event)
        return {"ok": True, "ignored": f"evento não relevante: {event}"}

    # 🔒 6. Validar status confirmado
    if status != "CONFIRMED":
        log_event(logger, "asaas_webhook_ignored", reason="status_not_confirmed", status=status)
        return {"ok": True, "ignored": f"status não confirmado: {status}"}

    # 🔥 7. Processar pagamento (idempotência no service)
    try:
        token = await create_token_for_paid_event(
            asaas_payment_id=payment_id,
            asaas_customer_id=customer_id,
            asaas_payment_link_id=payment_link_id,
        )
    except Exception as error:
        log_event(logger, "asaas_webhook_error", level=40, reason="payment_processing_failed", error=str(error))
        raise HTTPException(status_code=500, detail="Erro ao processar webhook")

    # 🔒 8. Caso duplicado ou falha controlada
    if not token:
        log_event(logger, "asaas_webhook_ignored", reason="payment_already_processed")
        return {"ok": True, "ignored": "pagamento já processado"}

    # 🔗 9. Gerar links
    claim_url = build_claim_url(token)
    telegram_start_url = build_telegram_start_link(token)

    log_event(
        logger,
        "asaas_webhook_processed",
        payment_id=payment_id,
        payment_link_id=payment_link_id,
        claim_url=claim_url,
        telegram_start_url=telegram_start_url,
    )

    return {
        "ok": True,
        "token": token,
        "claim_url": claim_url,
        "telegram_start_url": telegram_start_url,
    }


@app.get("/claim/{token}", response_class=HTMLResponse)
async def claim_token_page(token: str):
    telegram_url = build_telegram_start_link(token)

    html = f"""
    <html>
      <head>
        <meta charset="utf-8">
        <title>Ativar Profeta</title>
      </head>
      <body style="font-family: Arial, sans-serif; padding: 24px;">
        <h1>Pagamento aprovado</h1>
        <p>Seu acesso ao Profeta está pronto para ser ativado.</p>
        <p>
          <a href="{telegram_url}" style="display:inline-block;padding:12px 16px;background:#2AABEE;color:white;text-decoration:none;border-radius:8px;">
            Abrir no Telegram e ativar
          </a>
        </p>
        <p>Se o botão não abrir, copie e abra este link no navegador:</p>
        <pre>{telegram_url}</pre>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/go/{token}")
async def go_telegram(token: str):
    return RedirectResponse(url=build_telegram_start_link(token))
