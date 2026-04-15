import pytest
from fastapi.testclient import TestClient


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