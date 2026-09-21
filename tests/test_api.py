from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store


def client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    corpus = MemoryCorpus.from_json(default_sample_path())
    app = create_app(store, corpus, None, SearchConfig(enable_live=False))
    return TestClient(app)


def test_request_message_and_attachment_round_trip(tmp_path: Path):
    api = client(tmp_path)
    registered = api.post("/v1/auth/register", json={"email": "user@example.com", "password": "secret-pass"})
    assert registered.status_code == 200
    token = registered.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = api.post(
        "/v1/requests",
        headers=headers,
        json={
            "original_text": "أبي نجار يسوي دولاب",
            "need": "نجار دولاب",
            "notes": "شمال الرياض",
            "city": "الرياض",
            "attributes": {"item": "دولاب"},
            "recipients": [{"seller_id": "14371810", "seller_name": "نجار تفصيل وصيانه"}],
        },
    )
    assert created.status_code == 200
    request_id = created.json()["id"]
    uploaded = api.post(
        f"/v1/requests/{request_id}/attachments",
        headers=headers,
        files={"file": ("photo.jpg", b"fake-image", "image/jpeg")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["size_bytes"] == len(b"fake-image")
    message = api.post(
        f"/v1/requests/{request_id}/messages",
        headers=headers,
        json={"body": "كم السعر؟", "offer_amount": 1500, "offer_currency": "SAR"},
    )
    assert message.status_code == 200
    fetched = api.get(f"/v1/requests/{request_id}", headers=headers)
    body = fetched.json()
    assert body["attachments"]
    assert body["messages"][0]["offer"]["amount"] == 1500
    assert "rfq" not in fetched.text.lower()


def test_empty_recipient_is_rejected(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/register", json={"email": "a@example.com", "password": "secret-pass"}).json()["token"]
    response = api.post(
        "/v1/requests",
        headers={"Authorization": f"Bearer {token}"},
        json={"original_text": "test", "recipients": []},
    )
    assert response.status_code == 422


def test_search_trace_is_stored(tmp_path: Path):
    api = client(tmp_path)
    token = api.post("/v1/auth/register", json={"email": "b@example.com", "password": "secret-pass"}).json()["token"]
    search = api.post("/v1/search", json={"query": "أبي نجار"})
    assert search.status_code == 200
    assert search.json()["state"] == "CLARIFICATION_REQUIRED"
    trace_id = search.json()["trace_id"]
    trace = api.get(f"/v1/search/{trace_id}/trace", headers={"Authorization": f"Bearer {token}"})
    assert trace.status_code == 200
    assert trace.json()["state"] == "CLARIFICATION_REQUIRED"
