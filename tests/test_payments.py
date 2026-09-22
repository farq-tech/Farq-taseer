from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store


def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MOYASAR_PUBLISHABLE_KEY", "pk_test_publishable_for_unit_tests")
    monkeypatch.setenv("MOYASAR_SECRET_KEY", "sk_test_secret_for_unit_tests")
    monkeypatch.setenv("MOYASAR_APPLE_PAY_ASSOCIATION", "apple-pay-association-body")
    monkeypatch.delenv("MOYASAR_DISPLAY_NAME", raising=False)
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    corpus = MemoryCorpus.from_json(default_sample_path())
    app = create_app(store, corpus, None, SearchConfig(enable_live=False))
    return TestClient(app)


def _thread_with_offer(api: TestClient) -> tuple[str, dict]:
    token = api.post("/v1/auth/register", json={"email": "pay@example.com", "password": "secret-pass"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي سباك",
            "need": "سباك",
            "city": "الرياض",
            "recipients": [{"seller_id": "1", "seller_name": "سباك"}],
        },
    )
    request_id = created.json()["id"]
    reply_token = created.json()["reply_token"]
    api.post(
        f"/v1/seller/{reply_token}/messages",
        json={"body": "السعر", "offer_amount": 250, "offer_currency": "SAR"},
    )
    return request_id, headers


def test_payments_config_and_association(tmp_path: Path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    config = api.get("/v1/payments/config")
    assert config.status_code == 200
    body = config.json()
    assert body["enabled"] is True
    assert body["publishable_key"].startswith("pk_test_")
    assert "secret" not in body
    assert body["association_ready"] is True
    association = api.get("/.well-known/apple-developer-merchantid-domain-association")
    assert association.status_code == 200
    assert association.text == "apple-pay-association-body"
    health = api.get("/health").json()
    assert health["payments"] is True
    assert health["apple_pay_association"] is True


def test_confirm_payment_records_paid_offer(tmp_path: Path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    request_id, headers = _thread_with_offer(api)
    fake = {
        "id": "pay_test_123",
        "status": "paid",
        "amount": 25000,
        "currency": "SAR",
        "source": {"type": "applepay"},
    }
    with patch("farq.api.fetch_payment", return_value=fake):
        confirmed = api.post(
            "/v1/payments/confirm",
            headers=headers,
            json={"moyasar_id": "pay_test_123", "request_id": request_id},
        )
    assert confirmed.status_code == 200
    payload = confirmed.json()
    assert payload["payment"]["status"] == "paid"
    assert payload["payment"]["amount_halalas"] == 25000
    assert payload["request"]["payment"]["moyasar_id"] == "pay_test_123"
    thread = api.get(f"/v1/requests/{request_id}", headers=headers).json()
    assert any(item["sender_role"] == "system" for item in thread["messages"])


def test_confirm_rejects_amount_mismatch(tmp_path: Path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    request_id, headers = _thread_with_offer(api)
    fake = {"id": "pay_bad", "status": "paid", "amount": 100, "currency": "SAR", "source": {"type": "applepay"}}
    with patch("farq.api.fetch_payment", return_value=fake):
        response = api.post(
            "/v1/payments/confirm",
            headers=headers,
            json={"moyasar_id": "pay_bad", "request_id": request_id},
        )
    assert response.status_code == 422
