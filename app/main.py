import html as html_lib
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from telegram import Bot
from telegram.error import TelegramError

from app.config import ADMIN_SECRET, APP_NAME, ASAAS_WEBHOOK_TOKEN, BOT_USERNAME, ENV, PUBLIC_BASE_URL, TELEGRAM_BOT_TOKEN, is_production_environment, missing_settings
from app.db import engine, Base
import app.models  # noqa: F401
from app.observability import get_logger, log_event
from app.payment_service import (
    activate_with_payment_atomic,
    build_claim_url,
    build_telegram_start_link,
    create_token_for_paid_event,
    find_telegram_user_for_customer,
    payment_link_matches,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = []
    if is_production_environment():
        missing = missing_settings("ASAAS_WEBHOOK_TOKEN", "PUBLIC_BASE_URL", "BOT_USERNAME")
        if missing:
            raise RuntimeError(f"Configuração obrigatória ausente para produção: {', '.join(missing)}")

    log_event(
        logger,
        "api_starting",
        app_name=APP_NAME,
        env=ENV,
        bot_username=BOT_USERNAME,
        public_base_url=PUBLIC_BASE_URL,
        missing_settings=", ".join(missing),
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.bible_seed import check_and_seed_bible
    from app.db import SessionLocal as _SessionLocal
    try:
        await check_and_seed_bible(_SessionLocal)
    except Exception:
        logger.exception("Falha no bootstrap da Bíblia — sistema continuará com fallback")

    from app.core.session.factory import init_session_backend
    await init_session_backend()

    yield

    log_event(logger, "api_stopped", app_name=APP_NAME, env=ENV)


app = FastAPI(title="Profeta API", lifespan=lifespan)

from app.admin_api import router as _admin_ops_router  # noqa: E402
app.include_router(_admin_ops_router)


_ACTIVATION_MESSAGE = (
    "✅ Seu acesso foi ativado com sucesso.\n\n"
    "Você já pode receber um versículo com /versiculo, aprofundar com /explicar"
    " e retomar seu ritmo espiritual com /continuar."
)

_RENEWAL_MESSAGE = (
    "✅ Sua assinatura foi renovada com sucesso.\n\n"
    "Você já pode receber um versículo com /versiculo, aprofundar com /explicar"
    " e retomar seu ritmo espiritual com /continuar."
)


async def _send_bot_message(telegram_user_id: str, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        return False
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=telegram_user_id, text=text)
        return True
    except TelegramError as err:
        log_event(logger, "proactive_notification_telegram_error", telegram_user_id=telegram_user_id, error=str(err))
        return False


async def _process_direct_activation(
    telegram_user_id: str,
    payment_id: str,
    customer_id: str,
    value: Optional[float] = None,
) -> bool:
    """
    Flow A: API payment with externalReference. No token needed.
    Payment + User + Subscription are persisted atomically in a single transaction.
    If the commit fails, nothing is saved → Asaas retry will succeed on the next attempt.
    """
    try:
        activated = await activate_with_payment_atomic(payment_id, customer_id, telegram_user_id, value=value)
    except Exception as err:
        log_event(logger, "direct_activation_failed", telegram_user_id=telegram_user_id, error=str(err), level=40)
        return False

    if not activated:
        log_event(logger, "direct_activation_duplicate", payment_id=payment_id)
        return False

    await _send_bot_message(telegram_user_id, _ACTIVATION_MESSAGE)
    log_event(logger, "direct_activation_completed", telegram_user_id=telegram_user_id, payment_id=payment_id)
    return True


async def _try_proactive_renewal(
    telegram_user_id: str,
    token: str,
) -> bool:
    """
    Flow B (legacy): returning subscriber recognized by asaas_customer_id.
    Consumes token → activates subscription → notifies user.
    """
    from app.subscription_service import activate_subscription_for_user
    from app.token_service import consume_activation_token

    try:
        consumed = await consume_activation_token(token, telegram_user_id)
        if not consumed:
            log_event(logger, "proactive_renewal_token_consume_failed", telegram_user_id=telegram_user_id)
            return False

        await activate_subscription_for_user(telegram_user_id=telegram_user_id)
        await _send_bot_message(telegram_user_id, _RENEWAL_MESSAGE)
        log_event(logger, "proactive_renewal_activated", telegram_user_id=telegram_user_id)
        return True

    except Exception as err:
        log_event(logger, "proactive_renewal_failed", telegram_user_id=telegram_user_id, error=str(err))
        return False


@app.get("/")
async def root():
    return {"ok": True, "app": APP_NAME, "env": ENV}


@app.get("/health")
async def health():
    return {"status": "healthy", "app": APP_NAME, "env": ENV}


@app.post("/webhooks/asaas")
async def asaas_webhook(
    request: Request,
    asaas_access_token: Optional[str] = Header(default=None),
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
    payment_value = payment.get("value")
    billing_type = (payment.get("billingType") or "").strip()
    net_value = payment.get("netValue")

    log_event(
        logger,
        "asaas_webhook_received",
        event_name=event,
        payment_id=payment_id,
        customer_id=customer_id,
        payment_link_id=payment_link_id,
        status=status,
        payment_value=payment_value,
        billing_type=billing_type,
        net_value=net_value,
    )

    # 🔒 3. Validação básica do payment_id
    if not payment_id:
        log_event(logger, "asaas_webhook_ignored", reason="payment_id_missing")
        return {"ok": True, "ignored": "payment.id ausente"}

    if not payment_id.startswith("pay_"):
        log_event(logger, "asaas_webhook_ignored", reason="invalid_payment_id")
        return {"ok": True, "ignored": "payment_id inválido"}

    # 🔒 4. Validar evento e status (comum aos dois fluxos)
    if event != "PAYMENT_CONFIRMED":
        log_event(logger, "asaas_webhook_ignored", reason="irrelevant_event", event_name=event)
        return {"ok": True, "ignored": f"evento não relevante: {event}"}

    if status != "CONFIRMED":
        log_event(logger, "asaas_webhook_ignored", reason="status_not_confirmed", status=status)
        return {"ok": True, "ignored": f"status não confirmado: {status}"}

    # 🔒 5. Antifraude: rejeitar valores suspeitos (< R$1,00)
    if payment_value is not None:
        try:
            if float(payment_value) < 1.0:
                log_event(
                    logger,
                    "asaas_webhook_suspicious",
                    reason="value_below_minimum",
                    payment_id=payment_id,
                    payment_value=payment_value,
                    level=40,
                )
                return {"ok": True, "ignored": "valor abaixo do mínimo aceito"}
        except (TypeError, ValueError):
            pass

    _pay_value: Optional[float] = float(payment_value) if payment_value is not None else None

    # ── Determinar fluxo ────────────────────────────────────────────────────────
    # Fluxo A: pagamento criado via API com externalReference = telegram_user_id
    external_reference = (payment.get("externalReference") or "").strip()
    is_direct_flow = external_reference.isdigit() and not payment_link_id

    # Fluxo B (legado): link de pagamento compartilhado (backward compat)
    is_link_flow = bool(payment_link_id) and payment_link_matches(payment_link_id)

    # Fluxo C: compra de créditos — externalReference = "credits:{telegram_id}:{n}"
    is_credit_flow = external_reference.startswith("credits:") and not payment_link_id

    if is_credit_flow:
        parts = external_reference.split(":")
        if len(parts) == 3:
            _, credit_telegram_id, credits_str = parts
            try:
                credits_count = int(credits_str)
            except ValueError:
                credits_count = 0
            if credit_telegram_id and credits_count > 0:
                from app.credit_service import add_credits, get_credits
                added = await add_credits(
                    payment_id=payment_id,
                    telegram_id=credit_telegram_id,
                    credits=credits_count,
                    value=_pay_value,
                )
                if added:
                    balance = await get_credits(credit_telegram_id)
                    await _send_bot_message(
                        credit_telegram_id,
                        f"Pagamento confirmado 🙏\n\nVocê recebeu {credits_count} crédito(s).\nSaldo atual: {balance} crédito(s).",
                    )
                log_event(
                    logger, "asaas_webhook_credit_flow",
                    payment_id=payment_id,
                    telegram_id=credit_telegram_id,
                    credits=credits_count,
                    added=added,
                )
        return {"ok": True, "flow": "credits"}

    if not is_direct_flow and not is_link_flow:
        log_event(
            logger,
            "asaas_webhook_ignored",
            reason="no_matching_flow",
            external_reference=external_reference,
            payment_link_id=payment_link_id,
        )
        return {"ok": True, "ignored": "pagamento não identificado como Profeta"}

    # 🚀 Fluxo A — ativação direta (novos e renovações via API)
    if is_direct_flow:
        log_event(logger, "asaas_webhook_direct_flow", payment_id=payment_id, telegram_user_id=external_reference)
        activated = await _process_direct_activation(
            telegram_user_id=external_reference,
            payment_id=payment_id,
            customer_id=customer_id,
            value=_pay_value,
        )
        return {"ok": True, "flow": "direct", "activated": activated}

    # 🔑 Fluxo B — token + email (legado: link compartilhado)
    try:
        token = await create_token_for_paid_event(
            asaas_payment_id=payment_id,
            asaas_customer_id=customer_id,
            asaas_payment_link_id=payment_link_id,
            value=_pay_value,
        )
    except Exception as error:
        log_event(logger, "asaas_webhook_error", level=40, reason="payment_processing_failed", error=str(error))
        raise HTTPException(status_code=500, detail="Erro ao processar webhook")

    if not token:
        log_event(logger, "asaas_webhook_ignored", reason="payment_already_processed")
        return {"ok": True, "ignored": "pagamento já processado"}

    claim_url = build_claim_url(token)
    telegram_start_url = build_telegram_start_link(token)

    log_event(
        logger,
        "asaas_webhook_processed",
        payment_id=payment_id,
        customer_id=customer_id,
        payment_link_id=payment_link_id,
        claim_url=claim_url,
        telegram_start_url=telegram_start_url,
    )

    # Notificação proativa para renovações já mapeadas (customer_id → telegram_user_id)
    proactive = False
    if customer_id:
        known_telegram_id = await find_telegram_user_for_customer(customer_id)
        if known_telegram_id:
            proactive = await _try_proactive_renewal(known_telegram_id, token)

    return {
        "ok": True,
        "flow": "link",
        "token": token,
        "claim_url": claim_url,
        "telegram_start_url": telegram_start_url,
        "proactive_activation": proactive,
    }


@app.get("/claim/{token}", response_class=HTMLResponse)
async def claim_token_page(token: str):
    telegram_url = build_telegram_start_link(token)
    safe_url = html_lib.escape(telegram_url)

    page = f"""
    <html>
      <head>
        <meta charset="utf-8">
        <title>Ativar Profeta</title>
      </head>
      <body style="font-family: Arial, sans-serif; padding: 24px;">
        <h1>Pagamento aprovado</h1>
        <p>Seu acesso ao Profeta está pronto para ser ativado.</p>
        <p>
          <a href="{safe_url}" style="display:inline-block;padding:12px 16px;background:#2AABEE;color:white;text-decoration:none;border-radius:8px;">
            Abrir no Telegram e ativar
          </a>
        </p>
        <p>Se o botão não abrir, copie e abra este link no navegador:</p>
        <pre>{safe_url}</pre>
      </body>
    </html>
    """
    return HTMLResponse(content=page)


@app.get("/go/{token}")
async def go_telegram(token: str):
    return RedirectResponse(url=build_telegram_start_link(token))


# ── API Layer — Fase 5 ────────────────────────────────────────────────────────
#
# Bot Telegram → adapter → estas rotas → EngineFacade → JourneyEngine → serviços
#
# Cada rota aceita {tenant_id, user_id, payload?} e retorna o EngineOutput
# serializado. Permite multi-canal (WhatsApp, Web, App) sem alterar o engine.
#
# Auth: Fase 5 usa X-Engine-Key por tenant (env: ENGINE_API_KEY).
#       Por enquanto, a rota é interna; adicione auth antes de expor publicamente.

_ENGINE_API_KEY = os.getenv("ENGINE_API_KEY", "")


class _EngineRequest(BaseModel):
    tenant_id: str = "profeta"
    user_id: str
    payload: dict[str, Any] = {}


def _check_engine_auth(key: Optional[str]) -> None:
    """Valida X-Engine-Key quando ENGINE_API_KEY está configurada."""
    if _ENGINE_API_KEY and key != _ENGINE_API_KEY:
        raise HTTPException(status_code=401, detail="engine_key_invalid")


def _engine_response(output) -> JSONResponse:
    """Serializa EngineOutput para JSONResponse."""
    return JSONResponse(
        status_code=200 if output.success else 422,
        content={
            "success": output.success,
            "action": output.action,
            "data": output.data,
            "error": output.error,
        },
    )


@app.post("/api/verse")
async def api_verse(
    body: _EngineRequest,
    x_engine_key: Optional[str] = Header(default=None),
):
    """Seleciona um versículo aleatório para o usuário e atualiza o histórico."""
    _check_engine_auth(x_engine_key)
    from app.core.contracts import EngineInput
    from app.core.engine.engine_facade import engine
    result = await engine.execute(EngineInput(
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        action="verse_select",
        payload=body.payload,
    ))
    log_event(logger, "api_verse", tenant_id=body.tenant_id, user_id=body.user_id, success=result.success)
    return _engine_response(result)


@app.post("/api/explain")
async def api_explain(
    body: _EngineRequest,
    x_engine_key: Optional[str] = Header(default=None),
):
    """Gera explicação para o último versículo do usuário."""
    _check_engine_auth(x_engine_key)
    from app.core.contracts import EngineInput
    from app.core.engine.engine_facade import engine
    result = await engine.execute(EngineInput(
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        action="explanation_get",
        payload=body.payload,
    ))
    log_event(logger, "api_explain", tenant_id=body.tenant_id, user_id=body.user_id, success=result.success)
    return _engine_response(result)


@app.post("/api/continue")
async def api_continue(
    body: _EngineRequest,
    x_engine_key: Optional[str] = Header(default=None),
):
    """Avança para o próximo passo na progressão espiritual do usuário."""
    _check_engine_auth(x_engine_key)
    from app.core.contracts import EngineInput
    from app.core.engine.engine_facade import engine
    result = await engine.execute(EngineInput(
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        action="continue",
        payload=body.payload,
    ))
    log_event(logger, "api_continue", tenant_id=body.tenant_id, user_id=body.user_id, success=result.success)
    return _engine_response(result)


@app.post("/api/prayer")
async def api_prayer(
    body: _EngineRequest,
    x_engine_key: Optional[str] = Header(default=None),
):
    """Gera oração a partir do versículo e reflexão atuais do usuário."""
    _check_engine_auth(x_engine_key)
    from app.core.contracts import EngineInput
    from app.core.engine.engine_facade import engine
    result = await engine.execute(EngineInput(
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        action="prayer_get",
        payload=body.payload,
    ))
    log_event(logger, "api_prayer", tenant_id=body.tenant_id, user_id=body.user_id, success=result.success)
    return _engine_response(result)


# ── Admin dashboard ───────────────────────────────────────────────────────────
#
# Protegido por ADMIN_SECRET (env var). Se não configurado, permite acesso em
# dev e bloqueia em produção. Acesso: /admin/dashboard?secret=<ADMIN_SECRET>

def _check_admin_secret(secret: str) -> None:
    if ADMIN_SECRET:
        if secret != ADMIN_SECRET:
            raise HTTPException(status_code=403, detail="acesso negado")
    elif is_production_environment():
        raise HTTPException(status_code=403, detail="ADMIN_SECRET não configurado")


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(secret: str = ""):
    _check_admin_secret(secret)
    from app.admin_dashboard import ADMIN_DASHBOARD_HTML
    return HTMLResponse(content=ADMIN_DASHBOARD_HTML)


@app.get("/admin/api/finance")
async def admin_api_finance(secret: str = ""):
    _check_admin_secret(secret)
    from app.finance_service import get_finance_summary
    return await get_finance_summary()


@app.get("/admin/api/finance/transactions")
async def admin_api_finance_transactions(secret: str = "", limit: int = 50):
    _check_admin_secret(secret)
    from app.finance_service import get_credit_transactions_list
    return await get_credit_transactions_list(limit=min(limit, 200))


@app.get("/admin/api/finance/payments")
async def admin_api_finance_payments(secret: str = "", limit: int = 50):
    _check_admin_secret(secret)
    from app.finance_service import get_payments_list
    return await get_payments_list(limit=min(limit, 200))
