from fastapi.testclient import TestClient

from farq.api import create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.contracts import RequestRecipient
from farq.store import Store


def test_old_batch_link_keeps_authorization_and_routes_message_to_target(tmp_path, monkeypatch):
    monkeypatch.setenv("FARQ_RECIPIENTS_FROM_SEARCH", "0")
    store = Store(tmp_path / "db.sqlite", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False))
    api = TestClient(app)
    token = api.post("/v1/auth/register", json={"email": "owner@example.com", "password": "secret-pass", "name": "Owner"}).json()["token"]
    other = api.post("/v1/auth/register", json={"email": "other@example.com", "password": "secret-pass", "name": "Other"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    target = api.post("/v1/requests", headers=headers, json={"original_text": "أبواب", "need": "أبواب", "city": "الرياض", "recipients": [{"seller_id": "14371810", "seller_name": "مورد"}]}).json()["id"]
    old = "a" * 32
    store.canonical_request_id = lambda rid: target if rid == old else rid
    assert api.get(f"/v1/requests/{old}", headers=headers).json()["id"] == target
    assert api.get(f"/v1/requests/{old}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
    assert api.get(f"/v1/requests/{old}").status_code == 401
    response = api.post(f"/v1/requests/{old}/messages", headers=headers, json={"body": "تفصيل السعر؟"})
    assert response.status_code == 200, response.text
    messages = api.get(f"/v1/requests/{target}", headers=headers).json()["messages"]
    assert sum(m["body"] == "تفصيل السعر؟" for m in messages) == 1
    assert api.post(f"/v1/requests/{old}/messages", headers={"Authorization": f"Bearer {other}"}, json={"body": "لا يسمح"}).status_code == 404


def test_normal_requests_and_reserved_routes_are_not_resolved(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "uploads")
    calls = []
    store.canonical_request_id = lambda rid: calls.append(rid) or rid
    api = TestClient(create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False)))
    api.get("/v1/requests/open-count")
    api.get("/health")
    assert calls == []
