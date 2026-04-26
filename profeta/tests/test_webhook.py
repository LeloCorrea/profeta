import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture
def webhook_client(db_sessionmaker, monkeypatch):
    import app.main as main_module
    import app.payment_service as payment_service

    monkeypatch.setattr(main_module, "ASAAS_WEBHOOK_TOKEN", "test-webhook-token")
    monkeypatch.setattr(payment_service, "PAYMENT_LINK_ID", "plink_123")
    monkeypatch.setattr(payment_service, "PUBLIC_BASE_URL", "https://profeta.example.com")
    monkeypatch.setattr(payment_service, "BOT_USERNAME", "profeta_bot")

    with TestClient(main_module.app) as client:
        yield client


def test_webhook_confirms_payment_and_returns_links(webhook_client):
    payload = {
        "event": "PAYMENT_CONFIRMED",
        "payment": {
            "id": "pay_123",
            "customer": "cus_123",
            "paymentLink": "plink_123",
            "status": "CONFIRMED",
        },
    }

    response = webhook_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json=payload,
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["claim_url"].startswith("https://profeta.example.com/claim/")
    assert body["telegram_start_url"].startswith("https://t.me/profeta_bot?start=")


def test_webhook_is_idempotent(webhook_client):
    payload = {
        "event": "PAYMENT_CONFIRMED",
        "payment": {
            "id": "pay_999",
            "customer": "cus_999",
            "paymentLink": "plink_123",
            "status": "CONFIRMED",
        },
    }

    first = webhook_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json=payload,
    )
    second = webhook_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["ignored"] == "pagamento já processado"


def test_webhook_rejects_invalid_token(webhook_client):
    payload = {
        "event": "PAYMENT_CONFIRMED",
        "payment": {
            "id": "pay_777",
            "customer": "cus_777",
            "paymentLink": "plink_123",
            "status": "CONFIRMED",
        },
    }

    response = webhook_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "invalid"},
        json=payload,
    )

    assert response.status_code == 401


def test_webhook_returns_500_when_processing_fails(webhook_client, monkeypatch):
    import app.main as main_module

    async def raise_error(*args, **kwargs):
        raise RuntimeError("processing failed")

    monkeypatch.setattr(main_module, "create_token_for_paid_event", raise_error)

    payload = {
        "event": "PAYMENT_CONFIRMED",
        "payment": {
            "id": "pay_500",
            "customer": "cus_500",
            "paymentLink": "plink_123",
            "status": "CONFIRMED",
        },
    }

    response = webhook_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json=payload,
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Erro ao processar webhook"


# ── Fluxo A: ativação direta via externalReference ───────────────────────────

@pytest.fixture
def direct_client(db_sessionmaker, monkeypatch):
    """TestClient configurado para testar o fluxo A (externalReference)."""
    import app.main as main_module

    monkeypatch.setattr(main_module, "ASAAS_WEBHOOK_TOKEN", "test-webhook-token")

    with TestClient(main_module.app) as client:
        yield client


def test_direct_flow_returns_activated_true(direct_client):
    response = direct_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json={
            "event": "PAYMENT_CONFIRMED",
            "payment": {
                "id": "pay_direct_001",
                "customer": "cus_direct_001",
                "externalReference": "111222333",
                "status": "CONFIRMED",
            },
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["flow"] == "direct"
    assert body["activated"] is True


def test_direct_flow_is_idempotent(direct_client):
    payload = {
        "event": "PAYMENT_CONFIRMED",
        "payment": {
            "id": "pay_direct_idem",
            "customer": "cus_idem",
            "externalReference": "444555666",
            "status": "CONFIRMED",
        },
    }
    first = direct_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json=payload,
    )
    second = direct_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json=payload,
    )
    assert first.json()["activated"] is True
    assert second.json()["activated"] is False


def test_direct_flow_db_failure_returns_not_activated(direct_client, monkeypatch):
    """Se activate_with_payment_atomic lança exceção, activated=False e Asaas pode retentar."""
    import app.main as main_module

    async def broken(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(main_module, "activate_with_payment_atomic", broken)

    response = direct_client.post(
        "/webhooks/asaas",
        headers={"asaas-access-token": "test-webhook-token"},
        json={
            "event": "PAYMENT_CONFIRMED",
            "payment": {
                "id": "pay_broken_db",
                "customer": "cus_broken",
                "externalReference": "777888999",
                "status": "CONFIRMED",
            },
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["flow"] == "direct"
    assert body["activated"] is False


@pytest.mark.asyncio
async def test_activate_with_payment_atomic_creates_payment_user_subscription(db_sessionmaker):
    """Verifica atomicidade: Payment + User + Subscription criados na mesma transação."""
    from app.models import Payment, Subscription, User
    import app.payment_service as payment_service

    result = await payment_service.activate_with_payment_atomic(
        "pay_atom_001", "cus_atom_001", "100200300"
    )
    assert result is True

    async with db_sessionmaker() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.provider_payment_id == "pay_atom_001")
        )
        assert payment is not None
        assert payment.status == "CONFIRMED"
        assert payment.customer_id == "cus_atom_001"

        user = await session.scalar(
            select(User).where(User.telegram_user_id == "100200300")
        )
        assert user is not None
        assert user.asaas_customer_id == "cus_atom_001"

        sub = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        assert sub is not None
        assert sub.status == "active"
        assert sub.paid_until is not None


@pytest.mark.asyncio
async def test_activate_with_payment_atomic_is_idempotent(db_sessionmaker):
    """Segunda chamada com mesmo payment_id retorna False sem criar registros duplicados."""
    from app.models import Payment
    import app.payment_service as payment_service

    first = await payment_service.activate_with_payment_atomic(
        "pay_atom_idem", "cus_idem", "200300400"
    )
    second = await payment_service.activate_with_payment_atomic(
        "pay_atom_idem", "cus_idem", "200300400"
    )
    assert first is True
    assert second is False

    async with db_sessionmaker() as session:
        payments = (
            await session.execute(
                select(Payment).where(Payment.provider_payment_id == "pay_atom_idem")
            )
        ).scalars().all()
        assert len(payments) == 1


@pytest.mark.asyncio
async def test_activate_with_payment_atomic_renews_existing_subscription(db_sessionmaker):
    """Renovação: paid_until é estendida a partir da data atual de expiração."""
    from datetime import timedelta
    from app.models import Subscription, User
    import app.payment_service as payment_service

    # Primeira ativação
    await payment_service.activate_with_payment_atomic(
        "pay_renewal_01", "cus_renewal", "300400500"
    )

    # Segunda ativação (renovação)
    await payment_service.activate_with_payment_atomic(
        "pay_renewal_02", "cus_renewal", "300400500"
    )

    async with db_sessionmaker() as session:
        user = await session.scalar(
            select(User).where(User.telegram_user_id == "300400500")
        )
        sub = await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )
        assert sub.status == "active"
        # paid_until deve ser ~60 dias a partir de agora (dois ciclos de 30 dias)
        from datetime import datetime
        delta = sub.paid_until - datetime.utcnow()
        assert delta.days >= 58


# ── Fix #3: payment_link_matches fail closed ──────────────────────────────────

def test_payment_link_matches_rejects_when_not_configured(monkeypatch):
    """PAYMENT_LINK_ID ausente → False (fail closed). Nunca aceita qualquer link."""
    import app.payment_service as payment_service

    monkeypatch.setattr(payment_service, "PAYMENT_LINK_ID", "")

    assert payment_service.payment_link_matches("plink_qualquer") is False
    assert payment_service.payment_link_matches("") is False
    assert payment_service.payment_link_matches(None) is False


def test_payment_link_matches_accepts_correct_id(monkeypatch):
    """Com PAYMENT_LINK_ID configurado, aceita só o ID correto."""
    import app.payment_service as payment_service

    monkeypatch.setattr(payment_service, "PAYMENT_LINK_ID", "plink_correct")

    assert payment_service.payment_link_matches("plink_correct") is True
    assert payment_service.payment_link_matches("plink_other") is False
    assert payment_service.payment_link_matches("") is False


def test_link_flow_ignored_when_payment_link_id_not_configured(db_sessionmaker, monkeypatch):
    """Webhook com paymentLink presente mas PAYMENT_LINK_ID ausente → ignorado."""
    import app.main as main_module
    import app.payment_service as payment_service

    monkeypatch.setattr(main_module, "ASAAS_WEBHOOK_TOKEN", "test-webhook-token")
    monkeypatch.setattr(payment_service, "PAYMENT_LINK_ID", "")

    with TestClient(main_module.app) as client:
        response = client.post(
            "/webhooks/asaas",
            headers={"asaas-access-token": "test-webhook-token"},
            json={
                "event": "PAYMENT_CONFIRMED",
                "payment": {
                    "id": "pay_miscfg_001",
                    "customer": "cus_miscfg",
                    "paymentLink": "plink_qualquer",
                    "status": "CONFIRMED",
                },
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert "ignored" in body